import importlib
import importlib.util
import re
from pathlib import Path

import pytest

from instro.lib import registry
from instro.lib.registry import (
    CATEGORIES,
    DriverEntry,
    IdnPattern,
    check_registry,
    driver_registry,
    iter_driver_entries,
)

_SIMULATED_PREFIX = "Simulated"
# Found through the vendor SDK's own enumeration, not *IDN?.
_SDK_ENUMERATED = {"NIDAQDriver", "LabJackTSeriesDriver", "MCCDriver"}


@pytest.mark.parametrize(
    "category,driver_name,entry",
    [pytest.param(c, n, e, id=f"{c}:{n}") for c, n, e in iter_driver_entries()],
)
def test_every_registered_driver_is_discoverable(category: str, driver_name: str, entry: DriverEntry) -> None:
    """A driver in a vendor registry without an IDN pattern is configurable but invisible to discovery."""
    if driver_name.startswith(_SIMULATED_PREFIX) or driver_name in _SDK_ENUMERATED:
        assert entry.idn_patterns == ()
    else:
        assert entry.idn_patterns, f"{category}.{driver_name} has no IdnPattern; `instro discover` cannot recognize it"
    for pattern in entry.idn_patterns:
        assert pattern.vendors
        assert pattern.model.startswith("^"), f"{category}.{driver_name}: anchor the model regex {pattern.model!r}"
        re.compile(pattern.model)
    vendor_package = entry.path.rsplit(".", 2)[0]  # instro.daq.drivers.ni.nidaq.NIDAQDriver -> instro.daq.drivers.ni
    if importlib.util.find_spec(vendor_package) is None:
        pytest.skip(f"{vendor_package} is not installed")
    assert entry.class_name == entry.load().__name__


def test_idn_pattern_matching_rules() -> None:
    pattern = IdnPattern(("KEYSIGHT TECHNOLOGIES", "AGILENT TECHNOLOGIES"), r"^34461A", None)
    assert pattern.matches("Agilent Technologies", "34461A")
    assert not pattern.matches("Keysight Technologies", "X34461A")
    assert not pattern.matches("Siglent Technologies", "34461A")


def test_driver_registry_is_empty_for_categories_without_config() -> None:
    assert driver_registry("modbus") == {}


def test_categories_covers_every_vendor_registry() -> None:
    """A category missing from CATEGORIES is skipped by discovery and by the test above."""
    root = Path(registry.__file__).parents[1]
    with_registry = {
        p.parent.name for p in root.glob("*/config.py") if f"{p.parent.name.upper()}_VENDOR_REGISTRY" in p.read_text()
    }
    assert with_registry == set(CATEGORIES)


def test_daq_registry_resolves_vendor_drivers_by_name_without_importing_them() -> None:
    daq = driver_registry("daq")
    assert {"Keysight34980A", "NIDAQDriver", "LabJackTSeriesDriver", "MCCDriver"} <= set(daq)
    assert daq["NIDAQDriver"].path.startswith("instro.daq.drivers.ni.")
    assert daq["Keysight34980A"].idn_patterns  # a VISA DAQ: discoverable by *IDN? like any SCPI instrument


@pytest.mark.parametrize("category", CATEGORIES)
def test_registry_channel_counts_match_the_config_model(category: str) -> None:
    """Every registered pattern can produce a config block the category's driver config accepts."""
    config = importlib.import_module(f"instro.{category}.config")
    driver_config = getattr(config, "VisaDriverConfig", None)
    if driver_config is None:
        pytest.skip(f"{category} has no JSON driver config yet")
    check_registry(category, driver_registry(category), driver_config)


def test_check_registry_enforces_num_channels_in_both_directions() -> None:
    """The requirement is read from the category's driver config, so the registry and the schema cannot drift."""
    from pydantic import BaseModel

    class ChannelledDriverConfig(BaseModel):
        name: str
        num_channels: int

    class ChannellessDriverConfig(BaseModel):
        name: str

    counted = {"X": DriverEntry("m.X", (IdnPattern(("V",), r"^A", 2),))}
    uncounted = {"Y": DriverEntry("m.Y", (IdnPattern(("V",), r"^B"),))}

    check_registry("psu", counted, ChannelledDriverConfig)
    check_registry("dmm", uncounted, ChannellessDriverConfig)
    with pytest.raises(
        ValueError, match=r"psu\.Y: IdnPattern '\^B' needs num_channels; ChannelledDriverConfig requires it"
    ):
        check_registry("psu", uncounted, ChannelledDriverConfig)
    with pytest.raises(
        ValueError, match=r"dmm\.X: IdnPattern '\^A' carries num_channels; ChannellessDriverConfig has no such field"
    ):
        check_registry("dmm", counted, ChannellessDriverConfig)
