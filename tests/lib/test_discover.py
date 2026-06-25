import importlib
from unittest.mock import MagicMock, patch

import pytest

from instro.lib.discover import _IDN_MAP, scan_visa_resources


def _rm_mock(resources=()):
    mock = MagicMock()
    mock.list_resources.return_value = resources
    return mock


@pytest.mark.parametrize("category,class_name", {(v[0], v[1]) for v in _IDN_MAP.values()})
def test_idn_map_drivers_importable(category: str, class_name: str) -> None:
    module = importlib.import_module(f"instro.{category}.drivers")
    assert hasattr(module, class_name), f"{class_name} not found in instro.{category}.drivers"


def test_scan_empty_bench() -> None:
    mock_rm = _rm_mock(())
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        result = scan_visa_resources()
    assert result.instruments == []
    assert result.unrecognized == []
    assert result.errors == []


def test_scan_recognized_psu() -> None:
    mock_rm = _rm_mock(("USB0::0x15EF::0x0099::MY001::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = "B&K PRECISION,9115,12345,1.0"
            result = scan_visa_resources()

    assert len(result.instruments) == 1
    assert result.unrecognized == []
    assert result.errors == []
    info = result.instruments[0]
    assert info.resource == "USB0::0x15EF::0x0099::MY001::INSTR"
    assert info.category == "psu"
    assert info.driver_class_name == "BK9115"
    assert info.vendor_key == "bk_9115"
    assert info.num_channels == 1


def test_scan_recognized_dmm() -> None:
    mock_rm = _rm_mock(("USB0::0x05E6::0x2400::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = "KEITHLEY INSTRUMENTS,2400,12345,C30"
            result = scan_visa_resources()

    assert len(result.instruments) == 1
    info = result.instruments[0]
    assert info.category == "dmm"
    assert info.driver_class_name == "Keithley2400"
    assert info.vendor_key is None
    assert info.num_channels is None


def test_scan_unrecognized() -> None:
    mock_rm = _rm_mock(("USB0::0xABCD::0x1234::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = "UNKNOWN VENDOR,XYZ,000,1.0"
            result = scan_visa_resources()

    assert result.instruments == []
    assert len(result.unrecognized) == 1
    assert result.errors == []
    assert result.unrecognized[0].resource == "USB0::0xABCD::0x1234::INSTR"
    assert "UNKNOWN VENDOR" in result.unrecognized[0].idn.upper()


def test_scan_error() -> None:
    mock_rm = _rm_mock(("USB0::0x1234::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.open.side_effect = Exception("timeout")
            result = scan_visa_resources()

    assert result.instruments == []
    assert result.unrecognized == []
    assert len(result.errors) == 1
    assert result.errors[0].resource == "USB0::0x1234::INSTR"
    assert "timeout" in result.errors[0].message


def test_scan_asrl_skipped() -> None:
    mock_rm = _rm_mock(("ASRL1::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            result = scan_visa_resources()

    mock_driver_cls.assert_not_called()
    assert result.instruments == []
    assert result.unrecognized == []
    assert result.errors == []


def test_scan_mixed() -> None:
    resources = (
        "USB0::0x15EF::0x0099::MY001::INSTR",  # BK9115 — recognized PSU
        "USB0::0x05E6::0x2400::INSTR",  # Keithley — recognized DMM
        "USB0::0xABCD::0x1234::INSTR",  # unknown — unrecognized
        "USB0::0xDEAD::0xBEEF::INSTR",  # error
        "ASRL1::INSTR",  # serial — skipped
    )
    mock_rm = _rm_mock(resources)
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.side_effect = [
                "B&K PRECISION,9115,12345,1.0",
                "KEITHLEY INSTRUMENTS,2400,12345,C30",
                "UNKNOWN VENDOR,XYZ,000,1.0",
                Exception("timeout"),
            ]
            result = scan_visa_resources()

    assert len(result.instruments) == 2
    assert len(result.unrecognized) == 1
    assert len(result.errors) == 1
    assert result.instruments[0].category == "psu"
    assert result.instruments[1].category == "dmm"
