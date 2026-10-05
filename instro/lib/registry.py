"""Driver registries: config names to driver classes, plus the ``*IDN?`` patterns discovery matches against.

Each category's ``config.py`` owns a ``<CAT>_VENDOR_REGISTRY`` of :class:`DriverEntry` values. The JSON
config path resolves ``driver.name`` through it, and :func:`instro.lib.discover.match_idn` scans the same
entries' :class:`IdnPattern` tuples, so a driver registered once is both configurable and discoverable.
"""

from __future__ import annotations

import dataclasses
import importlib
import re
from collections.abc import Iterator

__all__ = [
    "CATEGORIES",
    "DriverEntry",
    "IdnPattern",
    "driver_registry",
    "iter_driver_entries",
    "resolve_driver_class",
]

# Categories whose config module exposes a <CAT>_VENDOR_REGISTRY. Categories without one
# (daq, i2c, modbus) resolve driver names through their drivers package instead.
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


def resolve_driver_class(category: str, driver_name: str) -> type:
    """Resolve a driver name to its class.

    Registered names load from the category registry. Categories without a registry (``daq``,
    ``i2c``, ``modbus``) resolve against ``instro.<category>.drivers``, which is where workspace
    vendor packages publish their classes, so ``resolve_driver_class("daq", "NIDAQ")`` works when
    ``instro-daq-ni`` is installed.

    Raises:
        KeyError: ``driver_name`` is neither registered nor exported by the drivers package.
    """
    entry = driver_registry(category).get(driver_name)
    if entry is not None:
        return entry.load()
    drivers = importlib.import_module(f"instro.{category}.drivers")
    cls = getattr(drivers, driver_name, None)
    if not isinstance(cls, type):
        raise KeyError(f"no driver named {driver_name!r} in category {category!r}")
    return cls
