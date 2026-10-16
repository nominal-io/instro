"""Driver registries: config names to driver classes, plus the ``*IDN?`` patterns discovery matches against.

Each category's ``config.py`` owns a ``<CAT>_VENDOR_REGISTRY`` of :class:`DriverEntry` values. The JSON
config path resolves ``driver.name`` through it, and :func:`instro.lib.discover.match_idn` scans the same
entries' :class:`IdnPattern` tuples, so a driver registered once is both configurable and discoverable.

Drivers that ship in vendor packages (NI, LabJack, MCC) are registered here too, in the core
``DAQ_VENDOR_REGISTRY``: the entry's import path points into the vendor package, so the name resolves
whenever that package is installed and fails with a plain ``ModuleNotFoundError`` when it is not.
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
CATEGORIES: tuple[str, ...] = ("psu", "dmm", "eload", "scope", "awg", "daq")


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
    """Verify every pattern in ``registry`` can produce a valid config block for ``category``.

    :meth:`~instro.lib.discover.DiscoveredInstrument.config_block` emits ``num_channels`` exactly
    when the matched :class:`IdnPattern` carries one, and each category's driver config either
    requires that field or forbids it (``extra="forbid"``). So a pattern must carry a count when
    the model requires one and must not when the model has no such field. The requirement is read
    from the model so the registry and the schema cannot drift apart.

    Run by the registry tests for every category rather than at import time: a bad pattern is a
    contributor mistake, and an import-time failure in one category would take ``match_idn()`` and
    ``instro discover`` down for all of them.

    Args:
        category: Category name, used in the error message (e.g. ``"psu"``).
        registry: The category's ``<CAT>_VENDOR_REGISTRY`` mapping.
        driver_config: The category's driver config Pydantic model; whether its ``num_channels``
            field is required decides which direction is enforced.

    Raises:
        ValueError: a pattern lacks ``num_channels`` where the config requires it, or carries one
            where the config has no such field.
    """
    field = driver_config.model_fields.get("num_channels")
    required = field is not None and field.is_required()
    for name, entry in registry.items():
        for pattern in entry.idn_patterns:
            if required and pattern.num_channels is None:
                raise ValueError(
                    f"{category}.{name}: IdnPattern {pattern.model!r} needs num_channels; "
                    f"{driver_config.__name__} requires it"
                )
            if not required and pattern.num_channels is not None:
                raise ValueError(
                    f"{category}.{name}: IdnPattern {pattern.model!r} carries num_channels; "
                    f"{driver_config.__name__} has no such field"
                )
