"""Driver registries: config names to driver classes, plus the ``*IDN?`` patterns discovery matches against.

Each category's ``config.py`` owns a ``<CAT>_VENDOR_REGISTRY`` of :class:`DriverEntry` values. The JSON
config path resolves ``driver.name`` through it, and :func:`instro.lib.discover.match_idn` scans the same
entries' :class:`IdnPattern` tuples, so a driver registered once is both configurable and discoverable.
"""

from __future__ import annotations

import dataclasses
import importlib
import re
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = [
    "CATEGORIES",
    "DriverEntry",
    "IdnPattern",
    "check_registry",
    "driver_registry",
    "iter_driver_entries",
]

# Categories whose config module exposes a <CAT>_VENDOR_REGISTRY.
CATEGORIES: tuple[str, ...] = ("psu", "dmm", "eload", "scope", "awg")


@dataclasses.dataclass(frozen=True)
class IdnPattern:
    """Which ``*IDN?`` replies a driver handles.

    Attributes:
        vendors: Case-insensitive substrings, one of which must appear in the manufacturer field
            (e.g. ``("KEYSIGHT TECHNOLOGIES", "AGILENT TECHNOLOGIES")``).
        model: Regular expression searched case-insensitively in the model field. Anchor it
            (``r"^DP83[12]"``) so a family pattern doesn't swallow a longer model number.
        num_channels: SCPI-programmable channel count for the matched model, or ``None`` where the
            category doesn't track channels (dmm, eload). Not necessarily the physical output count.

    Example::

        IdnPattern(vendors=("RIGOL TECHNOLOGIES",), model=r"^DP83[12]", num_channels=3)
    """

    vendors: tuple[str, ...]
    model: str
    num_channels: int | None = None

    def matches(self, manufacturer: str, model: str) -> bool:
        """True when ``manufacturer`` contains one of ``vendors`` and ``model`` matches the regex."""
        manufacturer = manufacturer.lower()
        if not any(v.lower() in manufacturer for v in self.vendors):
            return False
        return re.search(self.model, model, re.IGNORECASE) is not None


@dataclasses.dataclass(frozen=True)
class DriverEntry:
    """A registered driver: its import path and the instrument identities that map to it.

    Attributes:
        path: Dotted import path of the driver class, e.g. ``"instro.psu.drivers.bk_9115.BK9115"``.
        idn_patterns: ``*IDN?`` patterns discovery matches to this driver. Empty for drivers that
            have no physical identity (simulators).

    Example::

        DriverEntry(
            "instro.psu.drivers.bk_9115.BK9115",
            (IdnPattern(("B&K PRECISION",), r"^9115", 1),),
        )
    """

    path: str
    idn_patterns: tuple[IdnPattern, ...] = ()

    @property
    def class_name(self) -> str:
        """The bare class name, e.g. ``"BK9115"``."""
        return self.path.rsplit(".", 1)[1]

    def load(self) -> type:
        """Import and return the driver class."""
        module_path, class_name = self.path.rsplit(".", 1)
        cls: type = getattr(importlib.import_module(module_path), class_name)
        return cls


def driver_registry(category: str) -> dict[str, DriverEntry]:
    """Return ``category``'s ``<CAT>_VENDOR_REGISTRY``, or ``{}`` for a category without a config module."""
    module_name = f"instro.{category}.config"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        return {}
    registry: dict[str, DriverEntry] = getattr(module, f"{category.upper()}_VENDOR_REGISTRY", {})
    return registry


def iter_driver_entries() -> Iterator[tuple[str, str, DriverEntry]]:
    """Yield ``(category, driver_name, entry)`` for every registered driver, in category order."""
    for category in CATEGORIES:
        for name, entry in driver_registry(category).items():
            yield category, name, entry


def check_registry(category: str, registry: Mapping[str, DriverEntry], driver_config: type[BaseModel]) -> None:
    """Fail at import time if a registry entry cannot produce a valid config block for ``category``.

    Called at the bottom of each category ``config.py``. When the category's driver config model
    requires ``num_channels``, every :class:`IdnPattern` must carry one, because
    :meth:`~instro.lib.discover.DiscoveredInstrument.config_block` emits the field only when the
    matched pattern has it. The requirement is read from the model so the two never disagree.

    Args:
        category: Category name, used in the error message (e.g. ``"psu"``).
        registry: The category's ``<CAT>_VENDOR_REGISTRY`` mapping.
        driver_config: The category's driver config Pydantic model; whether its ``num_channels``
            field is required decides whether the check applies.

    Raises:
        ValueError: a pattern lacks ``num_channels`` for a category whose config requires it.
    """
    field = driver_config.model_fields.get("num_channels")
    if field is None or not field.is_required():
        return
    for name, entry in registry.items():
        for pattern in entry.idn_patterns:
            if pattern.num_channels is None:
                raise ValueError(
                    f"{category}.{name}: IdnPattern {pattern.model!r} needs num_channels; "
                    f"{driver_config.__name__} requires it"
                )
