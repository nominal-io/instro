"""Tests for InstroPSU.discover()."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

from instro.lib.discover import VisaInstrumentInfo, VisaScanError, VisaScanResult, VisaUnrecognizedInstrument
from instro.psu import InstroPSU
from instro.psu.config import VisaDriverConfig

_BK9115_INFO = VisaInstrumentInfo(
    resource="USB0::0x15EF::0x0099::MY001::INSTR",
    idn="B&K PRECISION,9115,12345,1.0",
    category="psu",
    driver_class_name="BK9115",
    vendor_key="bk_9115",
    num_channels=1,
)


def test_discover_returns_psuconfig_for_recognized_psu():
    result = VisaScanResult(instruments=[_BK9115_INFO], unrecognized=[], errors=[])
    with patch("instro.psu.psu.scan_visa_resources", return_value=result) as mock_scan:
        configs = InstroPSU.discover()

    mock_scan.assert_called_once_with(backend=None, timeout=2)
    assert len(configs) == 1
    config = configs[0]
    assert config.device.name == "USB0::0x15EF::0x0099::MY001::INSTR"
    assert isinstance(config.driver, VisaDriverConfig)
    assert config.driver.name == "BK9115"
    assert config.driver.num_channels == 1
    assert config.driver.visa.visa_resource == "USB0::0x15EF::0x0099::MY001::INSTR"


def test_discover_skips_non_psu_instruments():
    dmm_info = dataclasses.replace(
        _BK9115_INFO, category="dmm", driver_class_name="Keithley2400", vendor_key=None, num_channels=None
    )
    result = VisaScanResult(instruments=[dmm_info], unrecognized=[], errors=[])
    with patch("instro.psu.psu.scan_visa_resources", return_value=result):
        configs = InstroPSU.discover()

    assert configs == []


def test_discover_ignores_unrecognized_and_errors():
    result = VisaScanResult(
        instruments=[],
        unrecognized=[VisaUnrecognizedInstrument(resource="USB0::0xDEAD::INSTR", idn="UNKNOWN,XYZ")],
        errors=[VisaScanError(resource="USB0::0xBEEF::INSTR", message="timeout")],
    )
    with patch("instro.psu.psu.scan_visa_resources", return_value=result):
        configs = InstroPSU.discover()

    assert configs == []


def test_discover_passes_backend_and_timeout_through():
    result = VisaScanResult(instruments=[], unrecognized=[], errors=[])
    with patch("instro.psu.psu.scan_visa_resources", return_value=result) as mock_scan:
        InstroPSU.discover(backend="@py", timeout=5)

    mock_scan.assert_called_once_with(backend="@py", timeout=5)


def test_discover_propagates_explicit_backend_to_returned_config():
    result = VisaScanResult(instruments=[_BK9115_INFO], unrecognized=[], errors=[])
    with patch("instro.psu.psu.scan_visa_resources", return_value=result):
        configs = InstroPSU.discover(backend="@py")

    assert configs[0].driver.visa.visa_backend == "@py"
