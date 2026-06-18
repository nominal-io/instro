import importlib
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from instro.cli.discover import _IDN_MAP
from instro.cli.main import app

runner = CliRunner()


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

    with patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[mock_rm, Exception(), mock_rm]):
        with patch("instro.cli.discover.list_ports") as mock_lp:
            with patch("instro.cli.discover.VisaDriver") as mock_driver_cls:
                mock_lp.comports.return_value = [mock_port]
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
