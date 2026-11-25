"""Software tests for the Siglent SPS5042X PSU driver (contrib)."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.contrib.psu.drivers.siglent_sps5042x import SiglentSPS5042X
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig
from instro.psu.types import OperatingMode

NO_ERROR = "+0, No error"


@pytest.fixture
def sps_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.contrib.psu.drivers.siglent_sps5042x.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def sps_visa(sps_visa_cls: MagicMock) -> MagicMock:
    visa = sps_visa_cls.return_value
    visa.query.return_value = NO_ERROR
    return visa


@pytest.fixture
def sps(sps_visa_cls: MagicMock) -> SiglentSPS5042X:
    return SiglentSPS5042X("TCPIP0::192.168.1.100::INSTR")


def test_sps5042x_init_builds_visa_driver_from_resource(sps_visa_cls: MagicMock) -> None:
    SiglentSPS5042X("TCPIP0::192.168.1.100::INSTR")

    sps_visa_cls.assert_called_once_with("TCPIP0::192.168.1.100::INSTR")


def test_sps5042x_init_accepts_visa_config(sps_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::192.168.1.100::INSTR")

    SiglentSPS5042X(config)

    sps_visa_cls.assert_called_once_with(config)


def test_sps5042x_open_close_delegate_to_visa(sps: SiglentSPS5042X, sps_visa: MagicMock) -> None:
    sps.open()
    sps.close()

    sps_visa.open.assert_called_once()
    sps_visa.close.assert_called_once()


@pytest.mark.parametrize(
    "method_name,value,command,readback_query,readback",
    [
        ("set_voltage", 28.0, "VOLT CH1,28.000", "VOLT? CH1", "28.000000"),
        # The instrument reads 10 A back as 10.000004.
        ("set_current_limit", 10.0, "CURR CH1,10.000", "CURR? CH1", "10.000004"),
        ("set_overvoltage_protection_level", 31.5, "OVP CH1,31.500", "OVP? CH1", "31.500000"),
        ("set_overcurrent_protection_level", 12.25, "OCP CH1,12.250", "OCP? CH1", "12.250000"),
    ],
)
def test_sps5042x_setpoint_writes_are_read_back(
    sps: SiglentSPS5042X,
    sps_visa: MagicMock,
    method_name: str,
    value: float,
    command: str,
    readback_query: str,
    readback: str,
) -> None:
    sps_visa.query.side_effect = [NO_ERROR, readback, NO_ERROR]

    getattr(sps, method_name)(value, channel=1)

    sps_visa.write.assert_called_once_with(command)
    assert sps_visa.query.call_args_list == [call("SYST:ERR?"), call(readback_query), call("SYST:ERR?")]


def test_sps5042x_setpoint_clamped_by_instrument_raises(sps: SiglentSPS5042X, sps_visa: MagicMock) -> None:
    # The instrument accepts an out-of-range OVP without an error and applies its minimum instead.
    sps_visa.query.side_effect = [NO_ERROR, "4.000000", NO_ERROR]

    with pytest.raises(ValueError, match="overvoltage protection level 1.000 is out of range.*applied 4.000"):
        sps.set_overvoltage_protection_level(1.0, channel=1)


@pytest.mark.parametrize(
    "method_name,args,command",
    [
        ("output_enable", (True,), "OUTP ON"),
        ("output_enable", (False,), "OUTP OFF"),
        ("set_overcurrent_protection_enabled", (True,), "SYS:OCP:STAT CH1,ON"),
        ("set_overcurrent_protection_enabled", (False,), "SYS:OCP:STAT CH1,OFF"),
        ("clear_protection", (), "RESET:CHAN:PROT CH1"),
    ],
)
def test_sps5042x_writes_command_and_checks_errors(
    sps: SiglentSPS5042X,
    sps_visa: MagicMock,
    method_name: str,
    args: tuple,
    command: str,
) -> None:
    getattr(sps, method_name)(*args, channel=1)

    sps_visa.write.assert_called_once_with(command)
    sps_visa.query.assert_called_once_with("SYST:ERR?")


@pytest.mark.parametrize(
    "method_name,command",
    [
        ("get_voltage", "MEAS:VOLT? CH1"),
        ("get_current", "MEAS:CURR? CH1"),
        ("get_voltage_setpoint", "VOLT? CH1"),
        ("get_current_setpoint", "CURR? CH1"),
        ("get_overvoltage_protection_level", "OVP? CH1"),
        ("get_overcurrent_protection_level", "OCP? CH1"),
    ],
)
def test_sps5042x_float_queries_parse_response(
    sps: SiglentSPS5042X,
    sps_visa: MagicMock,
    method_name: str,
    command: str,
) -> None:
    sps_visa.query.side_effect = ["28.000000", NO_ERROR]

    assert getattr(sps, method_name)(channel=1) == pytest.approx(28.0)
    assert sps_visa.query.call_args_list == [call(command), call("SYST:ERR?")]


@pytest.mark.parametrize(
    "method_name,command",
    [("get_output_status", "OUTP?"), ("get_overcurrent_protection_enabled", "SYS:OCP:STAT? CH1")],
)
@pytest.mark.parametrize("reply,expected", [("1", True), ("0", False)])
def test_sps5042x_bool_queries_parse_numeric_reply(
    sps: SiglentSPS5042X,
    sps_visa: MagicMock,
    method_name: str,
    command: str,
    reply: str,
    expected: bool,
) -> None:
    sps_visa.query.side_effect = [reply, NO_ERROR]

    assert getattr(sps, method_name)(channel=1) is expected
    assert sps_visa.query.call_args_list == [call(command), call("SYST:ERR?")]


def test_sps5042x_get_operating_mode_reports_off_without_querying_mode(
    sps: SiglentSPS5042X,
    sps_visa: MagicMock,
) -> None:
    # MEAS:RUN:MODE? answers CV even with the output off, so it must not decide the OFF case.
    sps_visa.query.side_effect = ["0", NO_ERROR]

    assert sps.get_operating_mode(channel=1) is OperatingMode.OFF
    assert sps_visa.query.call_args_list == [call("OUTP?"), call("SYST:ERR?")]


@pytest.mark.parametrize(
    "reply,expected",
    [("CV", OperatingMode.CONSTANT_VOLTAGE), ("CC", OperatingMode.CONSTANT_CURRENT)],
)
def test_sps5042x_get_operating_mode_reads_regulation_when_output_on(
    sps: SiglentSPS5042X,
    sps_visa: MagicMock,
    reply: str,
    expected: OperatingMode,
) -> None:
    sps_visa.query.side_effect = ["1", NO_ERROR, reply, NO_ERROR]

    assert sps.get_operating_mode(channel=1) is expected
    assert sps_visa.query.call_args_list == [
        call("OUTP?"),
        call("SYST:ERR?"),
        call("MEAS:RUN:MODE? CH1"),
        call("SYST:ERR?"),
    ]


def test_sps5042x_get_operating_mode_rejects_unknown_reply(sps: SiglentSPS5042X, sps_visa: MagicMock) -> None:
    sps_visa.query.side_effect = ["1", NO_ERROR, "XX", NO_ERROR]

    with pytest.raises(RuntimeError, match="Unexpected Siglent SPS5042X PSU regulation mode: XX"):
        sps.get_operating_mode(channel=1)


def test_sps5042x_check_errors_raises_on_nonzero(sps: SiglentSPS5042X, sps_visa: MagicMock) -> None:
    sps_visa.query.return_value = "-113,Undefined header,SYST:OCP:STAT?"

    with pytest.raises(RuntimeError, match="Siglent SPS5042X reported error: -113"):
        sps.output_enable(True, channel=1)


def test_sps5042x_check_errors_accepts_unsigned_zero_acknowledgement(sps: SiglentSPS5042X, sps_visa: MagicMock) -> None:
    # RESET:CHAN:PROT succeeds but leaves "0,No error,<header>" rather than "+0, No error".
    sps_visa.query.return_value = "0,No error,RESET:CHAN:PROT"

    sps.clear_protection(channel=1)

    sps_visa.query.assert_called_once_with("SYST:ERR?")


@pytest.mark.parametrize(
    "method_name,args",
    [
        ("set_overvoltage_protection_enabled", (True,)),
        ("get_overvoltage_protection_enabled", ()),
        ("set_overvoltage_protection_delay", (0.1,)),
        ("get_overvoltage_protection_delay", ()),
        ("get_overvoltage_protection_tripped", ()),
        ("get_overcurrent_protection_tripped", ()),
        ("set_remote_sense_enabled", (True,)),
        ("get_remote_sense_enabled", ()),
    ],
)
def test_sps5042x_unsupported_methods_raise_without_scpi(
    sps: SiglentSPS5042X,
    sps_visa: MagicMock,
    method_name: str,
    args: tuple,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match=f"{method_name} is not supported by the Siglent SPS5042X"):
        getattr(sps, method_name)(*args, channel=1)

    sps_visa.write.assert_not_called()
    sps_visa.query.assert_not_called()


def test_sps5042x_rejects_channel_other_than_one_without_scpi(sps: SiglentSPS5042X, sps_visa: MagicMock) -> None:
    # The instrument would silently answer for a CH2 that doesn't exist.
    with pytest.raises(ValueError, match="channel must be 1"):
        sps.set_voltage(1.0, channel=2)

    sps_visa.write.assert_not_called()
    sps_visa.query.assert_not_called()
