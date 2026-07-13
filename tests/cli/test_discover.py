import importlib
import warnings
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from instro.cli.discover import _IDN_MAP
from instro.cli.main import app

runner = CliRunner()

_GPIB_DEGRADED_DEBUG_INFO = {
    "Version": "0.8.1",
    "ASRL INSTR": "Available via PySerial (3.5)",
    "GPIB INSTR": "Available ",
    "GPIB INTFC": [
        "gpib_ctypes is installed but could not locate the gpib library.",
        "Please manually load it using:",
        "  gpib_ctypes.gpib.gpib._load_lib(filename)",
    ],
}


@pytest.mark.parametrize("category,class_name", set(_IDN_MAP.values()))
def test_idn_map_drivers_importable(category: str, class_name: str) -> None:
    module = importlib.import_module(f"instro.{category}.drivers")
    assert hasattr(module, class_name), f"{class_name} not found in instro.{category}.drivers"


def _rm_mock(resources=()):
    mock = MagicMock()
    mock.list_resources.return_value = resources
    mock.resource_info.return_value = MagicMock(resource_name="")
    return mock


def test_discover_empty_bench():
    mock_rm = _rm_mock(())
    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[mock_rm, Exception(), mock_rm]):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            mock_lp.comports.return_value = []
            result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0


def test_discover_reports_ivi_backend():
    mock_rm = _rm_mock(())
    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[mock_rm]):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            mock_lp.comports.return_value = []
            result = runner.invoke(app, ["discover"])
    assert "backend: @ivi" in result.output
    mock_rm.visalib.get_debug_info.assert_not_called()


def test_discover_py_fallback_reports_degraded_interfaces():
    mock_rm = _rm_mock(())
    mock_rm.visalib.get_debug_info.return_value = _GPIB_DEGRADED_DEBUG_INFO
    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[OSError("no IVI backend"), mock_rm]):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            mock_lp.comports.return_value = []
            result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0
    assert "backend: @py" in result.output
    assert "GPIB: unavailable" in result.output
    assert "linux-gpib" in result.output
    assert "ASRL" not in result.output.replace("ASRL1", "")
    assert result.output.count("NO DEVICES FOUND") == 1
    assert result.output.count("GPIB: unavailable") == 2  # coverage line + NO DEVICES panel


def test_discover_explicit_py_backend_reports_degraded_interfaces():
    mock_rm = _rm_mock(("USB0::0x1234::0x5678::INSTR",))
    mock_rm.visalib.get_debug_info.return_value = _GPIB_DEGRADED_DEBUG_INFO
    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[mock_rm]):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            with patch("instro.cli.discover.VisaDriver") as mock_driver_cls:
                mock_lp.comports.return_value = []
                mock_driver_cls.return_value.query.return_value = "UNKNOWN VENDOR,XYZ,000,1.0"
                result = runner.invoke(app, ["discover", "--backend", "@py"])
    assert result.exit_code == 0
    assert "backend: @py" in result.output
    assert result.output.count("GPIB: unavailable") == 1  # devices found: no panel repeat
    assert "UNRECOGNIZED" in result.output


def test_discover_suppresses_gpib_warning_at_construction():
    mock_rm = _rm_mock(())

    def _warn_then_return(*args, **kwargs):
        warnings.warn("GPIB library not found. Please manually load it.", UserWarning, stacklevel=1)
        return mock_rm

    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=_warn_then_return):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            mock_lp.comports.return_value = []
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = runner.invoke(app, ["discover", "--backend", "@py"])
    assert result.exit_code == 0
    assert caught == []


def test_discover_mixed_bench():
    resources = ("USB0::0x05E6::0x9999::INSTR", "USB0::0x1234::0x5678::INSTR")
    mock_rm = _rm_mock(resources)
    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[mock_rm, Exception(), mock_rm]):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            with patch("instro.cli.discover.VisaDriver") as mock_driver_cls:
                mock_lp.comports.return_value = []
                mock_driver_cls.return_value.query.side_effect = [
                    "KEITHLEY INSTRUMENTS,2400,12345,C30",
                    "UNKNOWN VENDOR,XYZ,000,1.0",
                ]
                result = runner.invoke(app, ["discover"])
    assert "RECOGNIZED" in result.output
    assert "UNRECOGNIZED" in result.output


def test_discover_recognizes_scope():
    mock_rm = _rm_mock(("USB0::0xF4EC::0xEE38::INSTR",))
    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[mock_rm, Exception(), mock_rm]):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            with patch("instro.cli.discover.VisaDriver") as mock_driver_cls:
                mock_lp.comports.return_value = []
                mock_driver_cls.return_value.query.return_value = "Siglent Technologies,SDS1104X-E,SN,1.0"
                result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0
    assert "RECOGNIZED" in result.output
    assert "SiglentSDS1000XE" in result.output
    assert "scope" in result.output


def test_discover_failed_probe():
    mock_rm = _rm_mock(("USB0::0x1234::INSTR",))
    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[mock_rm, Exception(), mock_rm]):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            with patch("instro.cli.discover.VisaDriver") as mock_driver_cls:
                mock_lp.comports.return_value = []
                mock_driver_cls.return_value.open.side_effect = Exception("timeout")
                result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0


def test_discover_two_supported_one_unsupported_one_serial():
    resources = (
        "USB0::0x05E6::0x2400::INSTR",
        "USB0::0x0957::0x0607::INSTR",
        "USB0::0xABCD::0x9999::INSTR",
        "ASRL1::INSTR",  # skipped in main loop
    )
    mock_rm = _rm_mock(resources)
    mock_port = MagicMock()
    mock_port.device = "/dev/ttyUSB0"
    mock_port.manufacturer = "Arduino LLC"
    mock_port.product = "Arduino Uno"
    mock_port.description = "Arduino Uno"
    mock_port_no_product = MagicMock()
    mock_port_no_product.device = "COM3"
    mock_port_no_product.manufacturer = None
    mock_port_no_product.product = None
    mock_port_no_product.description = "USB Serial Port (COM3)"

    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[mock_rm, Exception(), mock_rm]):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            with patch("instro.cli.discover.VisaDriver") as mock_driver_cls:
                mock_lp.comports.return_value = [mock_port, mock_port_no_product]
                mock_driver_cls.return_value.query.side_effect = [
                    "KEITHLEY INSTRUMENTS,2400,12345,C30",
                    "AGILENT TECHNOLOGIES,34401A,MY12345,10.4",
                    "UNKNOWN VENDOR,XYZ,000,1.0",
                ]
                result = runner.invoke(app, ["discover"])

    assert result.exit_code == 0
    assert result.output.count("RECOGNIZED DEVICES") == 2
    assert result.output.count("UNRECOGNIZED DEVICES") == 1
    assert result.output.count("Keithley2400") == 1
    assert result.output.count("Agilent34401A") == 1
    assert "Arduino Uno" in result.output
    assert "COM3" in result.output
    assert "unknown" in result.output
