"""Software tests for the Keysight E36100-series PSU driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers import KeysightE36100


@pytest.fixture
def keysight_e36100_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.keysight_e36100.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def keysight_e36100_visa(keysight_e36100_visa_cls: MagicMock) -> MagicMock:
    visa = keysight_e36100_visa_cls.return_value
    visa.query.return_value = '+0,"No error"'
    return visa


@pytest.fixture
def keysight_e36100(keysight_e36100_visa_cls: MagicMock) -> KeysightE36100:
    return KeysightE36100("USB0::0x0957::0x1502::SN::INSTR")


def test_keysight_e36100_init_builds_visa_driver_from_resource(
    keysight_e36100_visa_cls: MagicMock,
) -> None:
    KeysightE36100("USB0::0x0957::0x1502::SN::INSTR")
    keysight_e36100_visa_cls.assert_called_once_with("USB0::0x0957::0x1502::SN::INSTR")


def test_keysight_e36100_init_accepts_prebuilt_connection_config(
    keysight_e36100_visa_cls: MagicMock,
) -> None:
    config = VisaConfig(visa_resource="USB0::keysight::INSTR")
    KeysightE36100(config)
    keysight_e36100_visa_cls.assert_called_once_with(config)


def test_keysight_e36100_open_close_delegate_to_visa(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.open()
    keysight_e36100_visa.open.assert_called_once()
    keysight_e36100.close()
    keysight_e36100_visa.close.assert_called_once()


@pytest.mark.parametrize(
    ("method_name", "args", "expected_write"),
    [
        ("set_voltage", (5.0,), "VOLT 5.000"),
        ("set_current_limit", (1.25,), "CURR 1.250"),
        ("output_enable", (True,), "OUTP:STAT ON"),
        ("output_enable", (False,), "OUTP:STAT OFF"),
        ("set_overvoltage_protection_level", (12.5,), "VOLT:PROT 12.500"),
        ("set_overvoltage_protection_enabled", (True,), "VOLT:PROT:STAT ON"),
        ("set_overvoltage_protection_enabled", (False,), "VOLT:PROT:STAT OFF"),
        ("set_overcurrent_protection_enabled", (True,), "CURR:PROT:STAT ON"),
        ("set_overcurrent_protection_enabled", (False,), "CURR:PROT:STAT OFF"),
        ("set_remote_sense_enabled", (True,), "VOLT:SENS EXT"),
        ("set_remote_sense_enabled", (False,), "VOLT:SENS INT"),
    ],
)
def test_keysight_e36100_manual_write_command_map(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
    expected_write: str,
) -> None:
    getattr(keysight_e36100, method_name)(*args, channel=1)
    keysight_e36100_visa.write.assert_called_once_with(expected_write)
    keysight_e36100_visa.query.assert_called_once_with("SYST:ERR?")


@pytest.mark.parametrize(
    ("method_name", "response", "expected_query", "expected_value"),
    [
        ("get_voltage", "1.23456789E+01", "MEAS:VOLT?", 12.3456789),
        ("get_current", "5.00000000E-01", "MEAS:CURR?", 0.5),
        ("get_output_status", "1", "OUTP:STAT?", True),
        ("get_output_status", "0", "OUTP:STAT?", False),
        ("get_overvoltage_protection_level", "1.25000000E+01", "VOLT:PROT:LEV?", 12.5),
        ("get_overvoltage_protection_enabled", "1", "VOLT:PROT:STAT?", True),
        ("get_overvoltage_protection_enabled", "0", "VOLT:PROT:STAT?", False),
        ("get_overcurrent_protection_enabled", "1", "CURR:PROT:STAT?", True),
        ("get_overcurrent_protection_enabled", "0", "CURR:PROT:STAT?", False),
        ("get_remote_sense_enabled", "1", "VOLT:SENS?", True),
        ("get_remote_sense_enabled", "0", "VOLT:SENS?", False),
    ],
)
def test_keysight_e36100_manual_query_command_map(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
    method_name: str,
    response: str,
    expected_query: str,
    expected_value: float | bool,
) -> None:
    keysight_e36100_visa.query.side_effect = [response, '+0,"No error"']
    result = getattr(keysight_e36100, method_name)(channel=1)

    if isinstance(expected_value, bool):
        assert result is expected_value
    else:
        assert result == pytest.approx(expected_value)
    assert keysight_e36100_visa.query.call_args_list == [call(expected_query), call("SYST:ERR?")]


def test_keysight_e36100_set_voltage_writes_checked(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_voltage(5.0, channel=1)
    keysight_e36100_visa.write.assert_called_once_with("VOLT 5.000")
    keysight_e36100_visa.query.assert_called_once_with("SYST:ERR?")


def test_keysight_e36100_get_voltage_parses_response(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1.23456789E+01", '+0,"No error"']
    assert keysight_e36100.get_voltage(channel=1) == pytest.approx(12.3456789)
    assert keysight_e36100_visa.query.call_args_list == [call("MEAS:VOLT?"), call("SYST:ERR?")]


def test_keysight_e36100_set_current_limit_writes_checked(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_current_limit(1.25, channel=1)
    keysight_e36100_visa.write.assert_called_once_with("CURR 1.250")
    keysight_e36100_visa.query.assert_called_once_with("SYST:ERR?")


def test_keysight_e36100_get_current_parses_response(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["5.00000000E-01", '+0,"No error"']
    assert keysight_e36100.get_current(channel=1) == pytest.approx(0.5)
    assert keysight_e36100_visa.query.call_args_list == [call("MEAS:CURR?"), call("SYST:ERR?")]


def test_keysight_e36100_output_enable_writes_checked(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.output_enable(True, channel=1)
    keysight_e36100.output_enable(False, channel=1)
    assert keysight_e36100_visa.write.call_args_list == [call("OUTP:STAT ON"), call("OUTP:STAT OFF")]


def test_keysight_e36100_get_output_status_parses(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1", '+0,"No error"']
    assert keysight_e36100.get_output_status(channel=1) is True
    keysight_e36100_visa.query.side_effect = ["0", '+0,"No error"']
    assert keysight_e36100.get_output_status(channel=1) is False


def test_keysight_e36100_set_overvoltage_protection_level_writes_level(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_overvoltage_protection_level(12.5, channel=1)
    keysight_e36100_visa.write.assert_called_once_with("VOLT:PROT 12.500")
    keysight_e36100_visa.query.assert_called_once_with("SYST:ERR?")


def test_keysight_e36100_get_overvoltage_protection_level_queries_level(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1.25000000E+01", '+0,"No error"']
    assert keysight_e36100.get_overvoltage_protection_level(channel=1) == pytest.approx(12.5)
    assert keysight_e36100_visa.query.call_args_list == [call("VOLT:PROT:LEV?"), call("SYST:ERR?")]


def test_keysight_e36100_set_overvoltage_protection_enabled_writes_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_overvoltage_protection_enabled(True, channel=1)
    keysight_e36100.set_overvoltage_protection_enabled(False, channel=1)
    assert keysight_e36100_visa.write.call_args_list == [
        call("VOLT:PROT:STAT ON"),
        call("VOLT:PROT:STAT OFF"),
    ]


def test_keysight_e36100_get_overvoltage_protection_enabled_parses_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1", '+0,"No error"']
    assert keysight_e36100.get_overvoltage_protection_enabled(channel=1) is True
    keysight_e36100_visa.query.side_effect = ["0", '+0,"No error"']
    assert keysight_e36100.get_overvoltage_protection_enabled(channel=1) is False


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overvoltage_protection_delay", (0.25,)),
        ("get_overvoltage_protection_delay", ()),
    ],
)
def test_keysight_e36100_overvoltage_protection_delay_raises_unsupported(
    keysight_e36100: KeysightE36100,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match=f"{method_name} is not supported by the Keysight E36100-series PSU",
    ):
        getattr(keysight_e36100, method_name)(*args, channel=1)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overcurrent_protection_level", (0.8,)),
        ("get_overcurrent_protection_level", ()),
    ],
)
def test_keysight_e36100_overcurrent_protection_level_raises_unsupported(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="no separate OCP level") as exc_info:
        getattr(keysight_e36100, method_name)(*args, channel=1)
    assert "CURR" in str(exc_info.value)
    assert "CURR:PROT:STAT" in str(exc_info.value)
    assert "set_current_limit" not in str(exc_info.value)
    assert "set_overcurrent_protection_enabled" not in str(exc_info.value)
    keysight_e36100_visa.write.assert_not_called()
    keysight_e36100_visa.query.assert_not_called()


def test_keysight_e36100_set_overcurrent_protection_enabled_writes_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_overcurrent_protection_enabled(True, channel=1)
    keysight_e36100.set_overcurrent_protection_enabled(False, channel=1)
    assert keysight_e36100_visa.write.call_args_list == [
        call("CURR:PROT:STAT ON"),
        call("CURR:PROT:STAT OFF"),
    ]


def test_keysight_e36100_get_overcurrent_protection_enabled_parses_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1", '+0,"No error"']
    assert keysight_e36100.get_overcurrent_protection_enabled(channel=1) is True
    keysight_e36100_visa.query.side_effect = ["0", '+0,"No error"']
    assert keysight_e36100.get_overcurrent_protection_enabled(channel=1) is False


def test_keysight_e36100_set_remote_sense_enabled_writes_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_remote_sense_enabled(True, channel=1)
    keysight_e36100.set_remote_sense_enabled(False, channel=1)
    assert keysight_e36100_visa.write.call_args_list == [call("VOLT:SENS EXT"), call("VOLT:SENS INT")]


def test_keysight_e36100_get_remote_sense_enabled_parses_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1", '+0,"No error"']
    assert keysight_e36100.get_remote_sense_enabled(channel=1) is True
    keysight_e36100_visa.query.side_effect = ["0", '+0,"No error"']
    assert keysight_e36100.get_remote_sense_enabled(channel=1) is False


def test_keysight_e36100_check_errors_accepts_unsigned_zero(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.return_value = '0,"No error"'
    keysight_e36100.set_voltage(1.0, channel=1)
    keysight_e36100_visa.query.assert_called_once_with("SYST:ERR?")


def test_keysight_e36100_check_errors_raises_on_nonzero(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="The Keysight E36100-series PSU reported error"):
        keysight_e36100.set_voltage(1.0, channel=1)
