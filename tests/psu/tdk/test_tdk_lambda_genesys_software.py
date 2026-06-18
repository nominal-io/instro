"""Software tests for the TDK Lambda Genesys-family PSU driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.psu.drivers import TDKLambdaGenesys

CHANNEL = 1


@pytest.fixture
def tdk_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.tdk_lambda_genesys.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def tdk_visa(tdk_visa_cls: MagicMock) -> MagicMock:
    visa = tdk_visa_cls.return_value
    visa.query.return_value = '+0,"No error"'
    return visa


@pytest.fixture
def tdk(tdk_visa_cls: MagicMock) -> TDKLambdaGenesys:
    return TDKLambdaGenesys("TCPIP0::tdk::INSTR")


def test_tdk_set_voltage_writes_checked(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    tdk.set_voltage(48.0, channel=CHANNEL)
    tdk_visa.write.assert_called_once_with("VOLT 48.000")
    tdk_visa.query.assert_called_once_with("SYSTEM:ERROR?")


def test_tdk_get_current_parses_response(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    tdk_visa.query.side_effect = ["2.500", '+0,"No error"']
    assert tdk.get_current(channel=CHANNEL) == pytest.approx(2.5)
    assert tdk_visa.query.call_args_list == [call("MEAS:CURR?"), call("SYSTEM:ERROR?")]


def test_tdk_get_output_status_parses_text_responses(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    tdk_visa.query.side_effect = ["ON", '+0,"No error"']
    assert tdk.get_output_status(channel=CHANNEL) is True
    tdk_visa.query.side_effect = ["OFF", '+0,"No error"']
    assert tdk.get_output_status(channel=CHANNEL) is False


def test_tdk_get_output_status_parses_numeric_responses(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    tdk_visa.query.side_effect = ["1", '+0,"No error"']
    assert tdk.get_output_status(channel=CHANNEL) is True
    tdk_visa.query.side_effect = ["0", '+0,"No error"']
    assert tdk.get_output_status(channel=CHANNEL) is False


def test_tdk_check_errors_raises_on_nonzero(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    tdk_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="TDK Lambda Genesys-family PSU reported error"):
        tdk.set_voltage(1.0, channel=CHANNEL)


def test_tdk_set_overvoltage_protection_level_writes_level(
    tdk: TDKLambdaGenesys,
    tdk_visa: MagicMock,
) -> None:
    tdk.set_overvoltage_protection_level(12.5, channel=CHANNEL)
    tdk_visa.write.assert_called_once_with("VOLT:PROT:LEV 12.500")
    tdk_visa.query.assert_called_once_with("SYSTEM:ERROR?")


def test_tdk_get_overvoltage_protection_level_queries_level(
    tdk: TDKLambdaGenesys,
    tdk_visa: MagicMock,
) -> None:
    tdk_visa.query.side_effect = ["12.500", '+0,"No error"']
    assert tdk.get_overvoltage_protection_level(channel=CHANNEL) == pytest.approx(12.5)
    assert tdk_visa.query.call_args_list == [call("VOLT:PROT:LEV?"), call("SYSTEM:ERROR?")]


def test_tdk_overvoltage_protection_enabled_raises_unsupported(
    tdk: TDKLambdaGenesys,
    tdk_visa: MagicMock,
) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_enabled is not supported by the TDK Lambda Genesys-family PSU",
    ):
        tdk.set_overvoltage_protection_enabled(True, channel=CHANNEL)
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_enabled is not supported by the TDK Lambda Genesys-family PSU",
    ):
        tdk.get_overvoltage_protection_enabled(channel=CHANNEL)
    tdk_visa.write.assert_not_called()
    tdk_visa.query.assert_not_called()


def test_tdk_overvoltage_protection_delay_raises_unsupported(
    tdk: TDKLambdaGenesys,
    tdk_visa: MagicMock,
) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_delay is not supported by the TDK Lambda Genesys-family PSU",
    ):
        tdk.set_overvoltage_protection_delay(0.25, channel=CHANNEL)
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_delay is not supported by the TDK Lambda Genesys-family PSU",
    ):
        tdk.get_overvoltage_protection_delay(channel=CHANNEL)
    tdk_visa.write.assert_not_called()
    tdk_visa.query.assert_not_called()


def test_tdk_overcurrent_protection_level_raises_unsupported(
    tdk: TDKLambdaGenesys,
    tdk_visa: MagicMock,
) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overcurrent_protection_level is not supported by the TDK Lambda Genesys-family PSU",
    ):
        tdk.set_overcurrent_protection_level(1.0, channel=CHANNEL)
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overcurrent_protection_level is not supported by the TDK Lambda Genesys-family PSU",
    ):
        tdk.get_overcurrent_protection_level(channel=CHANNEL)
    tdk_visa.write.assert_not_called()
    tdk_visa.query.assert_not_called()


def test_tdk_set_overcurrent_protection_enabled_writes_state(
    tdk: TDKLambdaGenesys,
    tdk_visa: MagicMock,
) -> None:
    tdk.set_overcurrent_protection_enabled(True, channel=CHANNEL)
    tdk.set_overcurrent_protection_enabled(False, channel=CHANNEL)
    assert tdk_visa.write.call_args_list == [
        call("CURR:PROT:STAT ON"),
        call("CURR:PROT:STAT OFF"),
    ]


def test_tdk_get_overcurrent_protection_enabled_parses_text_responses(
    tdk: TDKLambdaGenesys,
    tdk_visa: MagicMock,
) -> None:
    tdk_visa.query.side_effect = ["ON", '+0,"No error"']
    assert tdk.get_overcurrent_protection_enabled(channel=CHANNEL) is True
    tdk_visa.query.side_effect = ["OFF", '+0,"No error"']
    assert tdk.get_overcurrent_protection_enabled(channel=CHANNEL) is False


def test_tdk_get_overcurrent_protection_enabled_parses_numeric_responses(
    tdk: TDKLambdaGenesys,
    tdk_visa: MagicMock,
) -> None:
    tdk_visa.query.side_effect = ["1", '+0,"No error"']
    assert tdk.get_overcurrent_protection_enabled(channel=CHANNEL) is True
    tdk_visa.query.side_effect = ["0", '+0,"No error"']
    assert tdk.get_overcurrent_protection_enabled(channel=CHANNEL) is False


def test_tdk_remote_sense_raises_unsupported(
    tdk: TDKLambdaGenesys,
    tdk_visa: MagicMock,
) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_remote_sense_enabled is not supported by the TDK Lambda Genesys-family PSU",
    ):
        tdk.set_remote_sense_enabled(True, channel=CHANNEL)
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_remote_sense_enabled is not supported by the TDK Lambda Genesys-family PSU",
    ):
        tdk.get_remote_sense_enabled(channel=CHANNEL)
    tdk_visa.write.assert_not_called()
    tdk_visa.query.assert_not_called()


def test_tdk_rejects_invalid_channel(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="supports only channel 1"):
        tdk.set_voltage(1.0, channel=2)
    tdk_visa.write.assert_not_called()
    tdk_visa.query.assert_not_called()


def test_tdk_check_errors_accepts_unsigned_zero(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    tdk_visa.query.return_value = '0,"No error"'
    tdk.set_voltage(1.0, channel=CHANNEL)
    tdk_visa.query.assert_called_once_with("SYSTEM:ERROR?")
