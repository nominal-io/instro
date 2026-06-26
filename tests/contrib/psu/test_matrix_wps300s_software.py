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
    return wps_visa_cls.return_value


@pytest.fixture
def wps(wps_visa_cls: MagicMock) -> MatrixWPS300S:
    return MatrixWPS300S("ASRL1::INSTR", command_interval=0)


def test_wps_set_voltage_writes_command(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    with patch.object(wps, "_check_errors") as mock_check:
        wps.set_voltage(48.0, channel=CHANNEL)
    wps_visa.write.assert_called_once_with("VOLT 48.000")
    mock_check.assert_called_once()


def test_wps_set_current_limit_writes_command(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    with patch.object(wps, "_check_errors") as mock_check:
        wps.set_current_limit(2.5, channel=CHANNEL)
    wps_visa.write.assert_called_once_with("CURR 2.5000")
    mock_check.assert_called_once()


def test_wps_get_voltage_queries_measurement(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.return_value = "48.000"
    assert wps.get_voltage(channel=CHANNEL) == pytest.approx(48.0)
    wps_visa.query.assert_called_once_with("MEAS:VOLT?")


def test_wps_get_current_queries_measurement(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.return_value = "2.500"
    assert wps.get_current(channel=CHANNEL) == pytest.approx(2.5)
    wps_visa.query.assert_called_once_with("MEAS:CURR?")


def test_wps_output_enable_writes_on_off(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps.output_enable(True, channel=CHANNEL)
    wps.output_enable(False, channel=CHANNEL)
    assert wps_visa.write.call_args_list == [call("OUTP ON"), call("OUTP OFF")]


def test_wps_get_output_status_parses_text_responses(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.return_value = "ON"
    assert wps.get_output_status(channel=CHANNEL) is True
    wps_visa.query.return_value = "OFF"
    assert wps.get_output_status(channel=CHANNEL) is False


def test_wps_get_output_status_parses_numeric_responses(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.return_value = "1"
    assert wps.get_output_status(channel=CHANNEL) is True
    wps_visa.query.return_value = "0"
    assert wps.get_output_status(channel=CHANNEL) is False


def test_wps_set_overvoltage_protection_level_writes_command(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps.set_overvoltage_protection_level(55.0, channel=CHANNEL)
    wps_visa.write.assert_called_once_with("VOLT:PROT 55.000")


def test_wps_get_overvoltage_protection_level_queries_level(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps_visa.query.return_value = "55.000"
    assert wps.get_overvoltage_protection_level(channel=CHANNEL) == pytest.approx(55.0)
    wps_visa.query.assert_called_once_with("VOLT:PROT?")


def test_wps_set_overcurrent_protection_level_writes_command(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    wps.set_overcurrent_protection_level(3.0, channel=CHANNEL)
    wps_visa.write.assert_called_once_with("CURR:PROT 3.0000")


def test_wps_check_errors_is_called_after_command(wps: MatrixWPS300S, wps_visa: MagicMock) -> None:
    # _check_errors is currently disabled pending hardware validation, but verify it is wired up
    with patch.object(wps, "_check_errors") as mock_check:
        wps.set_voltage(1.0, channel=CHANNEL)
    mock_check.assert_called_once()


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
