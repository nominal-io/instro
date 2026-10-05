import re

import pytest

from instro.daq.drivers import Keysight34980A
from instro.lib.registry import (
    CATEGORIES,
    DriverEntry,
    IdnPattern,
    driver_registry,
    iter_driver_entries,
    resolve_driver_class,
)

_SIMULATED_PREFIX = "Simulated"


@pytest.mark.parametrize(
    "category,driver_name,entry",
    [pytest.param(c, n, e, id=f"{c}:{n}") for c, n, e in iter_driver_entries()],
)
def test_every_registered_driver_is_discoverable(category: str, driver_name: str, entry: DriverEntry) -> None:
    """A driver in a vendor registry without an IDN pattern is configurable but invisible to discovery."""
    if driver_name.startswith(_SIMULATED_PREFIX):
        assert entry.idn_patterns == ()
        return
    assert entry.idn_patterns, f"{category}.{driver_name} has no IdnPattern; `instro discover` cannot recognize it"
    for pattern in entry.idn_patterns:
        assert pattern.vendors
        assert pattern.model.startswith("^"), f"{category}.{driver_name}: anchor the model regex {pattern.model!r}"
        re.compile(pattern.model)
    assert entry.class_name == entry.load().__name__


def test_idn_pattern_matching_rules() -> None:
    pattern = IdnPattern(("KEYSIGHT TECHNOLOGIES", "AGILENT TECHNOLOGIES"), r"^34461A", None)
    assert pattern.matches("Agilent Technologies", "34461A")
    assert not pattern.matches("Keysight Technologies", "X34461A")
    assert not pattern.matches("Siglent Technologies", "34461A")


def test_driver_registry_is_empty_for_categories_without_config() -> None:
    assert driver_registry("daq") == {}
    assert set(CATEGORIES) == {"psu", "dmm", "eload", "scope", "awg"}


def test_resolve_driver_class_uses_registry_or_drivers_package() -> None:
    assert resolve_driver_class("psu", "BK9115").__name__ == "BK9115"
    assert resolve_driver_class("daq", "Keysight34980A") is Keysight34980A
    with pytest.raises(KeyError):
        resolve_driver_class("daq", "NoSuchDriver")
    # exported by instro.psu.drivers, but a registry category never falls back to the package
    with pytest.raises(KeyError, match="not registered|registered for category"):
        resolve_driver_class("psu", "PSUDriverBase")
