"""Software tests for the Matrix WPS300S-series PSU driver (contrib)."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.contrib.psu.drivers.matrix_wps300s import MatrixWPS300S
from instro.lib.exceptions import FeatureNotSupportedError

CHANNEL = 1


@pytest.fixture
def wps_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.contrib.psu.drivers.matrix_wps300s.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def wps_visa(wps_visa_cls: MagicMock) -> MagicMock:
    visa = wps_visa_cls.return_value
    visa.query.return_value = "No error"
    return visa


@pytest.fixture
def wps(wps_visa_cls: MagicMock) -> MatrixWPS300S:
    return MatrixWPS300S("ASRL1::INSTR", op_interval=0)


def test_wps_set_voltage_writes_command(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps.set_voltage(48.0, channel=CHANNEL)
    wps_visa.write.assert_called_once_with("VOLT 48.000")
    wps_visa.query.assert_called_once_with("SYST:ERR?")


def test_wps_set_current_limit_writes_command(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps.set_current_limit(2.5, channel=CHANNEL)
    wps_visa.write.assert_called_once_with("CURR 2.5000")
    wps_visa.query.assert_called_once_with("SYST:ERR?")


def test_wps_get_voltage_queries_measurement(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.side_effect = ["48.000", "No error"]
    assert wps.get_voltage(channel=CHANNEL) == pytest.approx(48.0)
    assert wps_visa.query.call_args_list == [call("MEAS:VOLT?"), call("SYST:ERR?")]


def test_wps_get_current_queries_measurement(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.side_effect = ["2.500", "No error"]
    assert wps.get_current(channel=CHANNEL) == pytest.approx(2.5)
    assert wps_visa.query.call_args_list == [call("MEAS:CURR?"), call("SYST:ERR?")]


def test_wps_output_enable_writes_on_off(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps.output_enable(True, channel=CHANNEL)
    wps.output_enable(False, channel=CHANNEL)
    assert wps_visa.write.call_args_list == [call("OUTP ON"), call("OUTP OFF")]


def test_wps_get_output_status_parses_text_responses(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.side_effect = ["ON", "No error"]
    assert wps.get_output_status(channel=CHANNEL) is True
    wps_visa.query.side_effect = ["OFF", "No error"]
    assert wps.get_output_status(channel=CHANNEL) is False


def test_wps_get_output_status_parses_numeric_responses(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.side_effect = ["1", "No error"]
    assert wps.get_output_status(channel=CHANNEL) is True
    wps_visa.query.side_effect = ["0", "No error"]
    assert wps.get_output_status(channel=CHANNEL) is False


def test_wps_set_overvoltage_protection_level_writes_command(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps.set_overvoltage_protection_level(55.0, channel=CHANNEL)
    wps_visa.write.assert_called_once_with("VOLT:PROT 55.000")


def test_wps_get_overvoltage_protection_level_queries_level(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.side_effect = ["55.000", "No error"]
    assert wps.get_overvoltage_protection_level(channel=CHANNEL) == pytest.approx(55.0)
    assert wps_visa.query.call_args_list == [call("VOLT:PROT?"), call("SYST:ERR?")]


def test_wps_set_overcurrent_protection_level_writes_command(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps.set_overcurrent_protection_level(3.0, channel=CHANNEL)
    wps_visa.write.assert_called_once_with("CURR:PROT 3.0000")


def test_wps_check_errors_raises_on_error_response(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.return_value = "Error 102"
    with pytest.raises(RuntimeError, match="Matrix WPS300S-series PSU reported error"):
        wps.set_voltage(1.0, channel=CHANNEL)


def test_wps_overvoltage_protection_delay_raises_unsupported(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_delay"):
        wps.set_overvoltage_protection_delay(0.1, channel=CHANNEL)
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_delay"):
        wps.get_overvoltage_protection_delay(channel=CHANNEL)
    wps_visa.write.assert_not_called()


def test_wps_remote_sense_raises_unsupported(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_remote_sense_enabled"):
        wps.set_remote_sense_enabled(True, channel=CHANNEL)
    with pytest.raises(FeatureNotSupportedError, match="get_remote_sense_enabled"):
        wps.get_remote_sense_enabled(channel=CHANNEL)
    wps_visa.write.assert_not_called()


def test_wps_rejects_invalid_channel(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="supports only channel 1"):
        wps.set_voltage(1.0, channel=2)
    wps_visa.write.assert_not_called()
    wps_visa.query.assert_not_called()
