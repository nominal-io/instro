"""Software tests for the Rigol DP800-series PSU driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers import RigolDP800

_NO_ERROR = '0,"No error"'


@pytest.fixture
def rigol_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.rigol_dp800.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def rigol_visa(rigol_visa_cls: MagicMock) -> MagicMock:
    visa = rigol_visa_cls.return_value
    visa.query.return_value = _NO_ERROR
    return visa


@pytest.fixture
def rigol(rigol_visa_cls: MagicMock) -> RigolDP800:
    return RigolDP800("TCPIP0::rigol::INSTR")


def _open_with_dp832_limits(rigol: RigolDP800, rigol_visa: MagicMock, reset_mock: bool = True) -> None:
    responses = ["RIGOL TECHNOLOGIES,DP832A,DP8B26AM00234,00.01.19", _NO_ERROR]
    for _ in range(3):
        responses.extend(
            [
                "0.000",
                _NO_ERROR,
                "32.000",
                _NO_ERROR,
                "0.000",
                _NO_ERROR,
                "3.200",
                _NO_ERROR,
                "0.001",
                _NO_ERROR,
                "33.000",
                _NO_ERROR,
                "0.001",
                _NO_ERROR,
                "3.300",
                _NO_ERROR,
            ]
        )

    rigol_visa.query.side_effect = responses
    rigol.open()
    if reset_mock:
        rigol_visa.reset_mock()
        rigol_visa.query.side_effect = None
        rigol_visa.query.return_value = _NO_ERROR


def test_rigol_init_builds_visa_driver_from_resource(rigol_visa_cls: MagicMock) -> None:
    RigolDP800("TCPIP0::rigol::INSTR")

    rigol_visa_cls.assert_called_once_with("TCPIP0::rigol::INSTR")


def test_rigol_init_accepts_prebuilt_connection_config(rigol_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::rigol::INSTR")
    RigolDP800(config)

    rigol_visa_cls.assert_called_once_with(config)


def test_rigol_open_close_delegate_to_visa(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    _open_with_dp832_limits(rigol, rigol_visa, reset_mock=False)
    rigol.close()

    rigol_visa.open.assert_called_once()
    rigol_visa.close.assert_called_once()


def test_rigol_open_caches_channel_limits(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    _open_with_dp832_limits(rigol, rigol_visa, reset_mock=False)

    assert rigol_visa.query.call_args_list[:18] == [
        call("*IDN?"),
        call(":SYST:ERR?"),
        call(":SOUR1:VOLT? MIN"),
        call(":SYST:ERR?"),
        call(":SOUR1:VOLT? MAX"),
        call(":SYST:ERR?"),
        call(":SOUR1:CURR? MIN"),
        call(":SYST:ERR?"),
        call(":SOUR1:CURR? MAX"),
        call(":SYST:ERR?"),
        call(":SOUR1:VOLT:PROT:LEV? MIN"),
        call(":SYST:ERR?"),
        call(":SOUR1:VOLT:PROT:LEV? MAX"),
        call(":SYST:ERR?"),
        call(":SOUR1:CURR:PROT:LEV? MIN"),
        call(":SYST:ERR?"),
        call(":SOUR1:CURR:PROT:LEV? MAX"),
        call(":SYST:ERR?"),
    ]


def test_rigol_set_voltage_writes_per_channel(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol.set_voltage(5.0, channel=2)

    rigol_visa.write.assert_called_once_with(":SOUR2:VOLT 5.000")
    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_get_voltage_uses_meas_command(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = ["12.000", '0,"No error"']

    assert rigol.get_voltage(channel=3) == pytest.approx(12.0)
    assert rigol_visa.query.call_args_list == [call(":MEAS:VOLT? CH3"), call(":SYST:ERR?")]


def test_rigol_set_current_limit_writes_per_channel(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol.set_current_limit(0.5, channel=2)

    rigol_visa.write.assert_called_once_with(":SOUR2:CURR 0.500")
    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_get_current_uses_meas_command(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = ["0.250", '0,"No error"']

    assert rigol.get_current(channel=1) == pytest.approx(0.25)
    assert rigol_visa.query.call_args_list == [call(":MEAS:CURR? CH1"), call(":SYST:ERR?")]


def test_rigol_output_enable_formats_per_channel(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol.output_enable(True, channel=1)
    rigol.output_enable(False, channel=2)

    assert rigol_visa.write.call_args_list == [call(":OUTP CH1,ON"), call(":OUTP CH2,OFF")]
    assert rigol_visa.query.call_args_list == [call(":SYST:ERR?"), call(":SYST:ERR?")]


def test_rigol_get_output_status_parses_state(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = ["ON", '0,"No error"']
    assert rigol.get_output_status(channel=1) is True

    rigol_visa.query.side_effect = ["OFF", '0,"No error"']
    assert rigol.get_output_status(channel=1) is False


def test_rigol_set_overvoltage_protection_level_writes_channel_command(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol.set_overvoltage_protection_level(12.0, channel=2)

    rigol_visa.write.assert_called_once_with(":SOUR2:VOLT:PROT 12.000")
    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_get_overvoltage_protection_level_queries_channel_command(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.side_effect = ["12.000", '0,"No error"']

    assert rigol.get_overvoltage_protection_level(channel=2) == pytest.approx(12.0)
    assert rigol_visa.query.call_args_list == [call(":SOUR2:VOLT:PROT:LEV?"), call(":SYST:ERR?")]


def test_rigol_set_overvoltage_protection_enabled_writes_state(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol.set_overvoltage_protection_enabled(True, channel=2)
    rigol.set_overvoltage_protection_enabled(False, channel=2)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR2:VOLT:PROT:STAT ON"),
        call(":SOUR2:VOLT:PROT:STAT OFF"),
    ]
    assert rigol_visa.query.call_args_list == [call(":SYST:ERR?"), call(":SYST:ERR?")]


def test_rigol_get_overvoltage_protection_enabled_parses_state(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.side_effect = ["ON", '0,"No error"']
    assert rigol.get_overvoltage_protection_enabled(channel=1) is True

    rigol_visa.query.side_effect = ["OFF", '0,"No error"']
    assert rigol.get_overvoltage_protection_enabled(channel=1) is False


def test_rigol_overvoltage_protection_delay_raises_unsupported(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="OVP delay command"):
        rigol.set_overvoltage_protection_delay(0.25, channel=1)

    with pytest.raises(FeatureNotSupportedError, match="OVP delay query"):
        rigol.get_overvoltage_protection_delay(channel=1)

    rigol_visa.write.assert_not_called()
    rigol_visa.query.assert_not_called()


def test_rigol_set_overcurrent_protection_level_writes_channel_command(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol.set_overcurrent_protection_level(2.0, channel=3)

    rigol_visa.write.assert_called_once_with(":SOUR3:CURR:PROT 2.000")
    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


@pytest.mark.parametrize(
    ("method_name", "value"),
    [
        ("set_voltage", -0.001),
        ("set_voltage", 32.001),
        ("set_current_limit", -0.001),
        ("set_current_limit", 3.201),
        ("set_overvoltage_protection_level", 0.000),
        ("set_overvoltage_protection_level", 33.001),
        ("set_overcurrent_protection_level", 0.000),
        ("set_overcurrent_protection_level", 3.301),
    ],
)
def test_rigol_setters_reject_cached_out_of_range_values(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
    method_name: str,
    value: float,
) -> None:
    _open_with_dp832_limits(rigol, rigol_visa)

    with pytest.raises(ValueError, match="out of range"):
        getattr(rigol, method_name)(value, channel=1)

    rigol_visa.write.assert_not_called()
    rigol_visa.query.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "value", "expected_command"),
    [
        ("set_voltage", 32.0, ":SOUR1:VOLT 32.000"),
        ("set_current_limit", 3.2, ":SOUR1:CURR 3.200"),
        ("set_overvoltage_protection_level", 33.0, ":SOUR1:VOLT:PROT 33.000"),
        ("set_overcurrent_protection_level", 3.3, ":SOUR1:CURR:PROT 3.300"),
    ],
)
def test_rigol_setters_accept_cached_maximum_values(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
    method_name: str,
    value: float,
    expected_command: str,
) -> None:
    _open_with_dp832_limits(rigol, rigol_visa)

    getattr(rigol, method_name)(value, channel=1)

    rigol_visa.write.assert_called_once_with(expected_command)
    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_invalid_cached_channel_surfaces_instrument_parameter_error(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    _open_with_dp832_limits(rigol, rigol_visa)
    rigol_visa.query.return_value = '-220,"Parameter error"'

    with pytest.raises(RuntimeError, match="Parameter error"):
        rigol.set_voltage(1.0, channel=4)

    rigol_visa.write.assert_called_once_with(":SOUR4:VOLT 1.000")
    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_set_remote_sense_enabled_surfaces_instrument_parameter_error(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.return_value = '-220,"Parameter error"'

    with pytest.raises(RuntimeError, match="Parameter error"):
        rigol.set_remote_sense_enabled(True, channel=1)

    rigol_visa.write.assert_called_once_with(":OUTP:SENS CH1,ON")
    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_get_overcurrent_protection_level_queries_channel_command(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.side_effect = ["2.000", '0,"No error"']

    assert rigol.get_overcurrent_protection_level(channel=3) == pytest.approx(2.0)
    assert rigol_visa.query.call_args_list == [call(":SOUR3:CURR:PROT:LEV?"), call(":SYST:ERR?")]


def test_rigol_set_overcurrent_protection_enabled_writes_state(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol.set_overcurrent_protection_enabled(True, channel=3)
    rigol.set_overcurrent_protection_enabled(False, channel=3)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR3:CURR:PROT:STAT ON"),
        call(":SOUR3:CURR:PROT:STAT OFF"),
    ]
    assert rigol_visa.query.call_args_list == [call(":SYST:ERR?"), call(":SYST:ERR?")]


def test_rigol_get_overcurrent_protection_enabled_parses_state(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.side_effect = ["1", '0,"No error"']
    assert rigol.get_overcurrent_protection_enabled(channel=1) is True

    rigol_visa.query.side_effect = ["0", '0,"No error"']
    assert rigol.get_overcurrent_protection_enabled(channel=1) is False


def test_rigol_set_remote_sense_enabled_writes_state(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol.set_remote_sense_enabled(True, channel=2)
    rigol.set_remote_sense_enabled(False, channel=2)

    assert rigol_visa.write.call_args_list == [
        call(":OUTP:SENS CH2,ON"),
        call(":OUTP:SENS CH2,OFF"),
    ]
    assert rigol_visa.query.call_args_list == [call(":SYST:ERR?"), call(":SYST:ERR?")]


def test_rigol_get_remote_sense_enabled_parses_state(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = ["ON", '0,"No error"']
    assert rigol.get_remote_sense_enabled(channel=2) is True

    rigol_visa.query.side_effect = ["OFF", '0,"No error"']
    assert rigol.get_remote_sense_enabled(channel=2) is False


def test_rigol_get_remote_sense_enabled_raises_unsupported_on_none(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.side_effect = ["NONE", '0,"No error"']

    with pytest.raises(FeatureNotSupportedError, match="remote sense is not supported"):
        rigol.get_remote_sense_enabled(channel=1)


def test_rigol_check_errors_accepts_unsigned_zero(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = '0,"No error"'

    rigol.set_voltage(1.0, channel=1)

    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_check_errors_accepts_signed_zero(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = '+0,"No error"'

    rigol.set_voltage(1.0, channel=1)

    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_check_errors_raises_on_nonzero(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = '-100,"Command error"'

    with pytest.raises(RuntimeError, match="Rigol DP800-series PSU reported error"):
        rigol.set_voltage(1.0, channel=1)


def test_rigol_write_checked_surfaces_documented_command_error(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.return_value = '-113,"Undefined header; keyword cannot be found"'

    with pytest.raises(RuntimeError, match="Undefined header"):
        rigol.output_enable(True, channel=1)

    rigol_visa.write.assert_called_once_with(":OUTP CH1,ON")
    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_query_checked_surfaces_documented_command_error(
    rigol: RigolDP800,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.side_effect = ["0.000", '-113,"Undefined header; keyword cannot be found"']

    with pytest.raises(RuntimeError, match="Undefined header"):
        rigol.get_voltage(channel=1)

    assert rigol_visa.query.call_args_list == [call(":MEAS:VOLT? CH1"), call(":SYST:ERR?")]
