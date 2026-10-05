import warnings
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from instro.cli.main import app
from instro.lib.discover import (
    DiscoveredInstrument,
    DiscoveryReport,
    ScanError,
    SerialPortInfo,
    SkippedResource,
    UnrecognizedInstrument,
)
from instro.lib.transports.visa import SerialConfig

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

_EMPTY_RESULT = DiscoveryReport(instruments=[], unrecognized=[], errors=[])


def _rm_mock():
    mock = MagicMock()
    mock.resource_info.return_value = MagicMock(resource_name="")
    return mock


def test_discover_empty_bench():
    mock_rm = _rm_mock()
    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", return_value=mock_rm),
        patch("instro.cli.discover.discover_instruments", return_value=_EMPTY_RESULT),
    ):
        result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0
    assert "NO DEVICES FOUND" in result.output


def test_discover_reports_ivi_backend():
    mock_rm = _rm_mock()
    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", return_value=mock_rm),
        patch("instro.cli.discover.discover_instruments", return_value=_EMPTY_RESULT),
    ):
        result = runner.invoke(app, ["discover"])
    assert "backend: @ivi" in result.output
    mock_rm.visalib.get_debug_info.assert_not_called()


def test_discover_py_fallback_reports_degraded_interfaces():
    mock_rm = _rm_mock()
    mock_rm.visalib.get_debug_info.return_value = _GPIB_DEGRADED_DEBUG_INFO
    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[OSError("no IVI backend"), mock_rm]),
        patch("instro.cli.discover.discover_instruments", return_value=_EMPTY_RESULT),
    ):
        result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0
    assert "backend: @py" in result.output
    assert "GPIB: unavailable" in result.output
    assert "linux-gpib" in result.output
    assert result.output.count("NO DEVICES FOUND") == 1
    assert result.output.count("GPIB: unavailable") == 2  # coverage line + NO DEVICES panel


def test_discover_explicit_py_backend_reports_degraded_interfaces():
    mock_rm = _rm_mock()
    mock_rm.visalib.get_debug_info.return_value = _GPIB_DEGRADED_DEBUG_INFO
    unrecognized_result = DiscoveryReport(
        instruments=[],
        unrecognized=[UnrecognizedInstrument(resource="USB0::0x1234::0x5678::INSTR", idn="UNKNOWN VENDOR,XYZ")],
        errors=[],
    )
    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", return_value=mock_rm),
        patch("instro.cli.discover.discover_instruments", return_value=unrecognized_result),
    ):
        result = runner.invoke(app, ["discover", "--backend", "@py"])
    assert result.exit_code == 0
    assert "backend: @py" in result.output
    assert result.output.count("GPIB: unavailable") == 1  # devices found: no panel repeat
    assert "UNRECOGNIZED" in result.output


def test_discover_suppresses_gpib_warning_at_construction():
    mock_rm = _rm_mock()

    def _warn_then_return(*args, **kwargs):
        warnings.warn("GPIB library not found. Please manually load it.", UserWarning, stacklevel=1)
        return mock_rm

    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=_warn_then_return),
        patch("instro.cli.discover.discover_instruments", return_value=_EMPTY_RESULT),
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        result = runner.invoke(app, ["discover", "--backend", "@py"])
    assert result.exit_code == 0
    assert caught == []


def test_discover_mixed_bench():
    mock_rm = _rm_mock()
    mixed_result = DiscoveryReport(
        instruments=[
            DiscoveredInstrument(
                resource="USB0::0x05E6::0x9999::INSTR",
                idn="KEITHLEY INSTRUMENTS,2400,12345,C30",
                category="dmm",
                driver_name="Keithley2400",
                num_channels=None,
            )
        ],
        unrecognized=[UnrecognizedInstrument(resource="USB0::0x1234::0x5678::INSTR", idn="UNKNOWN VENDOR,XYZ")],
        errors=[],
    )
    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", return_value=mock_rm),
        patch("instro.cli.discover.discover_instruments", return_value=mixed_result),
    ):
        result = runner.invoke(app, ["discover"])
    assert "RECOGNIZED" in result.output
    assert "UNRECOGNIZED" in result.output
    assert "Keithley2400" in result.output


def test_discover_failed_probe():
    mock_rm = _rm_mock()
    error_result = DiscoveryReport(
        instruments=[],
        unrecognized=[],
        errors=[ScanError(resource="USB0::0x1234::INSTR", message="timeout")],
    )
    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", return_value=mock_rm),
        patch("instro.cli.discover.discover_instruments", return_value=error_result),
    ):
        result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0
    assert "ERRORS" in result.output
    assert "timeout" in result.output


def test_discover_failed_probe_prefers_hint_over_raw_message():
    mock_rm = _rm_mock()
    error_result = DiscoveryReport(
        instruments=[],
        unrecognized=[],
        errors=[
            ScanError(
                resource="USB0::0x1234::INSTR",
                message="VI_ERROR_SYSTEM_ERROR (-1073807360): raw pyvisa detail",
                hint="permission denied - check udev rules",
            )
        ],
    )
    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", return_value=mock_rm),
        patch("instro.cli.discover.discover_instruments", return_value=error_result),
    ):
        result = runner.invoke(app, ["discover"])
    assert "permission denied - check udev rules" in result.output
    assert "raw pyvisa detail" not in result.output


def test_discover_two_supported_one_unsupported_one_serial():
    mock_rm = _rm_mock()
    mixed_result = DiscoveryReport(
        instruments=[
            DiscoveredInstrument(
                resource="USB0::0x05E6::0x2400::INSTR",
                idn="KEITHLEY INSTRUMENTS,2400,12345,C30",
                category="dmm",
                driver_name="Keithley2400",
                num_channels=None,
            ),
            DiscoveredInstrument(
                resource="ASRL/dev/ttyUSB1::INSTR",
                idn="AGILENT TECHNOLOGIES,34401A,MY12345,10.4",
                category="dmm",
                driver_name="Agilent34401A",
                num_channels=None,
                serial_config=SerialConfig(),
            ),
        ],
        unrecognized=[UnrecognizedInstrument(resource="USB0::0xABCD::0x9999::INSTR", idn="UNKNOWN VENDOR,XYZ")],
        errors=[
            ScanError(
                resource="ASRL/dev/ttyUSB0::INSTR",
                message="VI_ERROR_TMO",
                hint="no *IDN? reply at 9600 baud, 8N1, no flow control; set this port's serial_config manually",
            )
        ],
        skipped=[SkippedResource(resource="ASRL3::INSTR", reason="COM3 is not a USB serial port")],
        serial_ports=[
            SerialPortInfo(
                "/dev/ttyUSB0", "ASRL/dev/ttyUSB0::INSTR", "Arduino Uno", "Arduino LLC", "Arduino Uno", 0x2341, 0x43
            ),
            SerialPortInfo("/dev/ttyUSB1", "ASRL/dev/ttyUSB1::INSTR", "USB-Serial", "FTDI", "FT232R", 0x403, 0x6001),
            SerialPortInfo("COM3", "ASRL3::INSTR", "USB Serial Port (COM3)"),
        ],
    )

    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", return_value=mock_rm),
        patch("instro.cli.discover.discover_instruments", return_value=mixed_result),
    ):
        result = runner.invoke(app, ["discover"])

    assert result.exit_code == 0
    assert "RECOGNIZED DEVICES" in result.output
    assert "UNRECOGNIZED DEVICES" in result.output
    assert "SERIAL PORTS NOT IDENTIFIED" in result.output
    assert "ERRORS" not in result.output  # serial failures go to the serial table, not ERRORS
    assert result.output.count("Keithley2400") == 1
    assert result.output.count("Agilent34401A") == 1
    assert "9600 baud, 8N1" in result.output
    assert "Arduino Uno" in result.output
    assert "serial_config manually" in result.output
    assert "COM3" in result.output
    assert "unknown" in result.output
    assert "FT232R" not in result.output  # identified: listed as a recognized device instead


def test_discover_passes_requested_backend_to_discovery():
    """The resolved backend is for the header only; discovery gets what the user asked for so records keep the fallback."""
    mock_rm = _rm_mock()
    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", side_effect=[OSError("no IVI backend"), mock_rm]),
        patch("instro.cli.discover.discover_instruments", return_value=_EMPTY_RESULT) as run,
    ):
        runner.invoke(app, ["discover"])
    run.assert_called_once_with(backend=None)
    with (
        patch("instro.cli.discover.pyvisa.ResourceManager", return_value=mock_rm),
        patch("instro.cli.discover.discover_instruments", return_value=_EMPTY_RESULT) as run,
    ):
        runner.invoke(app, ["discover", "--backend", "@py"])
    run.assert_called_once_with(backend="@py")
