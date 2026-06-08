"""Tests for PSU drivers (driver-owned VisaDriver transport) and InstroPSU composition."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import SerialConfig, VisaConfig
from instro.psu import InstroPSU, PSUDriverBase
from instro.psu.drivers import (
    BK9115,
    BK9140,
    KeysightE36100,
    RigolDP800,
    SiglentSPD3303,
    SimulatedPSU,
    TDKLambdaGenesys,
)

# --- PSUDriverBase ---


class _BaseOnlyPSUDriver(PSUDriverBase):
    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_voltage(self, voltage: float, channel: int = 1) -> None:
        pass

    def get_voltage(self, channel: int = 1) -> float:
        return 0.0

    def set_current_limit(self, current_limit: float, channel: int = 1) -> None:
        pass

    def get_current(self, channel: int = 1) -> float:
        return 0.0

    def output_enable(self, enable: bool, channel: int = 1) -> None:
        pass

    def get_output_status(self, channel: int = 1) -> bool:
        return False


@pytest.fixture
def base_only_psu_driver() -> _BaseOnlyPSUDriver:
    return _BaseOnlyPSUDriver()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overvoltage_protection_level", (12.0,)),
        ("get_overvoltage_protection_level", ()),
        ("set_overvoltage_protection_enabled", (True,)),
        ("get_overvoltage_protection_enabled", ()),
        ("set_overvoltage_protection_delay", (0.25,)),
        ("get_overvoltage_protection_delay", ()),
    ],
)
def test_psu_driver_base_ovp_methods_raise_not_implemented(
    base_only_psu_driver: _BaseOnlyPSUDriver,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for _BaseOnlyPSUDriver"):
        getattr(base_only_psu_driver, method_name)(*args)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overcurrent_protection_level", (1.0,)),
        ("get_overcurrent_protection_level", ()),
        ("set_overcurrent_protection_enabled", (True,)),
        ("get_overcurrent_protection_enabled", ()),
    ],
)
def test_psu_driver_base_ocp_methods_raise_not_implemented(
    base_only_psu_driver: _BaseOnlyPSUDriver,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for _BaseOnlyPSUDriver"):
        getattr(base_only_psu_driver, method_name)(*args)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_remote_sense_enabled", (True,)),
        ("get_remote_sense_enabled", ()),
    ],
)
def test_psu_driver_base_remote_sense_methods_raise_not_implemented(
    base_only_psu_driver: _BaseOnlyPSUDriver,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for _BaseOnlyPSUDriver"):
        getattr(base_only_psu_driver, method_name)(*args)


# --- BK9115 ---


@pytest.fixture
def bk_single_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.bk_9115.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def bk_single_visa(bk_single_visa_cls: MagicMock) -> MagicMock:
    visa = bk_single_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def bk_single(bk_single_visa_cls: MagicMock) -> BK9115:
    return BK9115("USB0::0xFFFF::0x9115::SN::INSTR")


def test_bk_single_init_builds_visa_driver_from_resource(bk_single_visa_cls: MagicMock) -> None:
    BK9115("USB0::0xFFFF::0x9115::SN::INSTR")
    bk_single_visa_cls.assert_called_once_with("USB0::0xFFFF::0x9115::SN::INSTR")


def test_bk_single_init_accepts_prebuilt_connection_config(bk_single_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="USB0::example::INSTR")
    BK9115(config)
    bk_single_visa_cls.assert_called_once_with(config)


def test_bk_single_open_close_delegate_to_visa(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single.open()
    bk_single_visa.open.assert_called_once()
    bk_single.close()
    bk_single_visa.close.assert_called_once()


def test_bk_single_set_voltage_writes_checked(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single.set_voltage(5.0)
    bk_single_visa.write.assert_called_once_with("VOLT 5.000")
    bk_single_visa.query.assert_called_once_with("SYST:ERR?")


def test_bk_single_get_voltage_parses_response(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single_visa.query.side_effect = ["12.345", '0,"No error"']
    assert bk_single.get_voltage() == pytest.approx(12.345)
    assert bk_single_visa.query.call_args_list == [call("MEAS:VOLT?"), call("SYST:ERR?")]


def test_bk_single_set_current_limit_writes_checked(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single.set_current_limit(1.25)
    bk_single_visa.write.assert_called_once_with("CURR 1.250")
    bk_single_visa.query.assert_called_once_with("SYST:ERR?")


def test_bk_single_get_current_parses_response(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single_visa.query.side_effect = ["0.500", '0,"No error"']
    assert bk_single.get_current() == pytest.approx(0.5)


def test_bk_single_output_enable_writes_checked(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single.output_enable(True)
    bk_single_visa.write.assert_called_once_with("OUTP:STAT ON")
    bk_single.output_enable(False)
    assert bk_single_visa.write.call_args_list[-1] == call("OUTP:STAT OFF")


def test_bk_single_get_output_status_parses(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single_visa.query.side_effect = ["1", '0,"No error"']
    assert bk_single.get_output_status() is True
    bk_single_visa.query.side_effect = ["0", '0,"No error"']
    assert bk_single.get_output_status() is False


def test_bk_single_check_errors_raises_on_nonzero(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        bk_single.set_voltage(1.0)


# --- BK9140 ---


@pytest.fixture
def bk_multi_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.bk_9140.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def bk_multi_visa(bk_multi_visa_cls: MagicMock) -> MagicMock:
    visa = bk_multi_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def bk_multi(bk_multi_visa_cls: MagicMock) -> BK9140:
    return BK9140("USB0::0xFFFF::0x9140::SN::INSTR")


def test_bk_multi_init_builds_visa_driver_from_resource(bk_multi_visa_cls: MagicMock) -> None:
    BK9140("USB0::0xFFFF::0x9140::SN::INSTR")
    bk_multi_visa_cls.assert_called_once_with("USB0::0xFFFF::0x9140::SN::INSTR")


def test_bk_multi_set_voltage_selects_channel_then_writes(bk_multi: BK9140, bk_multi_visa: MagicMock) -> None:
    bk_multi.set_voltage(3.3, channel=2)
    assert bk_multi_visa.write.call_args_list == [call("INST 1"), call("VOLT 3.300")]
    bk_multi_visa.query.assert_called_once_with("SYST:ERR?")


def test_bk_multi_selects_channel_one_before_first_write(bk_multi: BK9140, bk_multi_visa: MagicMock) -> None:
    bk_multi.set_voltage(3.3, channel=1)
    assert bk_multi_visa.write.call_args_list == [call("INST 0"), call("VOLT 3.300")]


def test_bk_multi_skips_channel_select_when_active(bk_multi: BK9140, bk_multi_visa: MagicMock) -> None:
    bk_multi.set_voltage(3.3, channel=1)
    bk_multi.set_current_limit(0.5, channel=1)
    assert bk_multi_visa.write.call_args_list == [call("INST 0"), call("VOLT 3.300"), call("CURR 0.500")]


def test_bk_multi_get_voltage_returns_float(bk_multi: BK9140, bk_multi_visa: MagicMock) -> None:
    bk_multi_visa.query.side_effect = ["7.890", '0,"No error"']
    assert bk_multi.get_voltage(channel=2) == pytest.approx(7.89)
    assert bk_multi_visa.write.call_args_list == [call("INST 1")]
    assert bk_multi_visa.query.call_args_list == [call("MEAS:VOLT?"), call("SYST:ERR?")]


def test_bk_multi_get_output_status_parses(bk_multi: BK9140, bk_multi_visa: MagicMock) -> None:
    bk_multi_visa.query.side_effect = ["1", '0,"No error"']
    assert bk_multi.get_output_status(channel=1) is True


def test_bk_multi_check_errors_raises_on_nonzero(bk_multi: BK9140, bk_multi_visa: MagicMock) -> None:
    bk_multi_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        bk_multi.set_voltage(1.0)


# --- KeysightE36100 ---


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
    getattr(keysight_e36100, method_name)(*args)
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
    result = getattr(keysight_e36100, method_name)()

    if isinstance(expected_value, bool):
        assert result is expected_value
    else:
        assert result == pytest.approx(expected_value)
    assert keysight_e36100_visa.query.call_args_list == [call(expected_query), call("SYST:ERR?")]


def test_keysight_e36100_set_voltage_writes_checked(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_voltage(5.0)
    keysight_e36100_visa.write.assert_called_once_with("VOLT 5.000")
    keysight_e36100_visa.query.assert_called_once_with("SYST:ERR?")


def test_keysight_e36100_get_voltage_parses_response(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1.23456789E+01", '+0,"No error"']
    assert keysight_e36100.get_voltage() == pytest.approx(12.3456789)
    assert keysight_e36100_visa.query.call_args_list == [call("MEAS:VOLT?"), call("SYST:ERR?")]


def test_keysight_e36100_set_current_limit_writes_checked(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_current_limit(1.25)
    keysight_e36100_visa.write.assert_called_once_with("CURR 1.250")
    keysight_e36100_visa.query.assert_called_once_with("SYST:ERR?")


def test_keysight_e36100_get_current_parses_response(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["5.00000000E-01", '+0,"No error"']
    assert keysight_e36100.get_current() == pytest.approx(0.5)
    assert keysight_e36100_visa.query.call_args_list == [call("MEAS:CURR?"), call("SYST:ERR?")]


def test_keysight_e36100_output_enable_writes_checked(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.output_enable(True)
    keysight_e36100.output_enable(False)
    assert keysight_e36100_visa.write.call_args_list == [call("OUTP:STAT ON"), call("OUTP:STAT OFF")]


def test_keysight_e36100_get_output_status_parses(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1", '+0,"No error"']
    assert keysight_e36100.get_output_status() is True
    keysight_e36100_visa.query.side_effect = ["0", '+0,"No error"']
    assert keysight_e36100.get_output_status() is False


def test_keysight_e36100_set_overvoltage_protection_level_writes_level(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_overvoltage_protection_level(12.5)
    keysight_e36100_visa.write.assert_called_once_with("VOLT:PROT 12.500")
    keysight_e36100_visa.query.assert_called_once_with("SYST:ERR?")


def test_keysight_e36100_get_overvoltage_protection_level_queries_level(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1.25000000E+01", '+0,"No error"']
    assert keysight_e36100.get_overvoltage_protection_level() == pytest.approx(12.5)
    assert keysight_e36100_visa.query.call_args_list == [call("VOLT:PROT:LEV?"), call("SYST:ERR?")]


def test_keysight_e36100_set_overvoltage_protection_enabled_writes_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_overvoltage_protection_enabled(True)
    keysight_e36100.set_overvoltage_protection_enabled(False)
    assert keysight_e36100_visa.write.call_args_list == [
        call("VOLT:PROT:STAT ON"),
        call("VOLT:PROT:STAT OFF"),
    ]


def test_keysight_e36100_get_overvoltage_protection_enabled_parses_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1", '+0,"No error"']
    assert keysight_e36100.get_overvoltage_protection_enabled() is True
    keysight_e36100_visa.query.side_effect = ["0", '+0,"No error"']
    assert keysight_e36100.get_overvoltage_protection_enabled() is False


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
        getattr(keysight_e36100, method_name)(*args)


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
        getattr(keysight_e36100, method_name)(*args)
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
    keysight_e36100.set_overcurrent_protection_enabled(True)
    keysight_e36100.set_overcurrent_protection_enabled(False)
    assert keysight_e36100_visa.write.call_args_list == [
        call("CURR:PROT:STAT ON"),
        call("CURR:PROT:STAT OFF"),
    ]


def test_keysight_e36100_get_overcurrent_protection_enabled_parses_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1", '+0,"No error"']
    assert keysight_e36100.get_overcurrent_protection_enabled() is True
    keysight_e36100_visa.query.side_effect = ["0", '+0,"No error"']
    assert keysight_e36100.get_overcurrent_protection_enabled() is False


def test_keysight_e36100_set_remote_sense_enabled_writes_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100.set_remote_sense_enabled(True)
    keysight_e36100.set_remote_sense_enabled(False)
    assert keysight_e36100_visa.write.call_args_list == [call("VOLT:SENS EXT"), call("VOLT:SENS INT")]


def test_keysight_e36100_get_remote_sense_enabled_parses_state(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.side_effect = ["1", '+0,"No error"']
    assert keysight_e36100.get_remote_sense_enabled() is True
    keysight_e36100_visa.query.side_effect = ["0", '+0,"No error"']
    assert keysight_e36100.get_remote_sense_enabled() is False


def test_keysight_e36100_check_errors_accepts_unsigned_zero(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.return_value = '0,"No error"'
    keysight_e36100.set_voltage(1.0)
    keysight_e36100_visa.query.assert_called_once_with("SYST:ERR?")


def test_keysight_e36100_check_errors_raises_on_nonzero(
    keysight_e36100: KeysightE36100,
    keysight_e36100_visa: MagicMock,
) -> None:
    keysight_e36100_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="The Keysight E36100-series PSU reported error"):
        keysight_e36100.set_voltage(1.0)


# --- RigolDP800 ---


@pytest.fixture
def rigol_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.rigol_dp800.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def rigol_visa(rigol_visa_cls: MagicMock) -> MagicMock:
    visa = rigol_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def rigol(rigol_visa_cls: MagicMock) -> RigolDP800:
    return RigolDP800("TCPIP0::rigol::INSTR")


def test_rigol_set_voltage_writes_per_channel(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol.set_voltage(5.0, channel=2)
    rigol_visa.write.assert_called_once_with(":SOUR2:VOLT 5.000")
    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_rigol_get_voltage_uses_meas_command(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = ["12.000", '0,"No error"']
    assert rigol.get_voltage(channel=3) == pytest.approx(12.0)
    assert rigol_visa.query.call_args_list == [call(":MEAS:VOLT? CH3"), call(":SYST:ERR?")]


def test_rigol_output_enable_formats_per_channel(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol.output_enable(True, channel=1)
    rigol.output_enable(False, channel=2)
    assert rigol_visa.write.call_args_list == [call(":OUTP CH1,ON"), call(":OUTP CH2,OFF")]


def test_rigol_get_output_status_parses_on(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = ["ON", '0,"No error"']
    assert rigol.get_output_status(channel=1) is True


def test_rigol_check_errors_raises_on_nonzero(rigol: RigolDP800, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="Rigol PSU reported error"):
        rigol.set_voltage(1.0)


# --- SiglentSPD3303 ---


@pytest.fixture
def siglent_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.siglent_spd3303.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def siglent_visa(siglent_visa_cls: MagicMock) -> MagicMock:
    visa = siglent_visa_cls.return_value
    visa.query.return_value = '+0,"No error"'
    return visa


@pytest.fixture
def siglent(siglent_visa_cls: MagicMock) -> SiglentSPD3303:
    return SiglentSPD3303("USB0::Siglent::SN::INSTR")


def test_siglent_set_voltage_writes_per_channel(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent.set_voltage(2.5, channel=1)
    siglent_visa.write.assert_called_once_with("CH1:VOLT 2.500")
    siglent_visa.query.assert_called_once_with("SYST:ERR?")


def test_siglent_get_voltage_returns_float(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent_visa.query.side_effect = ["3.300", '+0,"No error"']
    assert siglent.get_voltage(channel=2) == pytest.approx(3.3)
    assert siglent_visa.query.call_args_list == [call("MEAS:VOLT? CH2"), call("SYST:ERR?")]


def test_siglent_output_enable_formats_per_channel(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent.output_enable(True, channel=2)
    siglent_visa.write.assert_called_once_with("OUTP CH2,ON")


def test_siglent_get_output_status_decodes_bitmap(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    # bit 4 (ch1_enable) set, bit 5 (ch2_enable) not set -> 0x10
    siglent_visa.query.side_effect = ["10", '+0,"No error"']
    assert siglent.get_output_status(channel=1) is True
    siglent_visa.query.side_effect = ["20", '+0,"No error"']
    assert siglent.get_output_status(channel=2) is True
    siglent_visa.query.side_effect = ["00", '+0,"No error"']
    assert siglent.get_output_status(channel=1) is False


def test_siglent_check_errors_raises_on_nonzero(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="Siglent PSU reported error"):
        siglent.set_voltage(1.0)


# --- TDKLambdaGenesys ---


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
    tdk.set_voltage(48.0)
    tdk_visa.write.assert_called_once_with("VOLT 48.000")
    tdk_visa.query.assert_called_once_with("SYSTEM:ERROR?")


def test_tdk_get_current_parses_response(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    tdk_visa.query.side_effect = ["2.500", '+0,"No error"']
    assert tdk.get_current() == pytest.approx(2.5)
    assert tdk_visa.query.call_args_list == [call("MEAS:CURR?"), call("SYSTEM:ERROR?")]


def test_tdk_get_output_status_parses_on(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    tdk_visa.query.side_effect = ["ON", '+0,"No error"']
    assert tdk.get_output_status() is True
    tdk_visa.query.side_effect = ["OFF", '+0,"No error"']
    assert tdk.get_output_status() is False


def test_tdk_check_errors_raises_on_nonzero(tdk: TDKLambdaGenesys, tdk_visa: MagicMock) -> None:
    tdk_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="TDK Lambda PSU reported error"):
        tdk.set_voltage(1.0)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overvoltage_protection_level", (12.0,)),
        ("get_overvoltage_protection_level", ()),
        ("set_overvoltage_protection_enabled", (True,)),
        ("get_overvoltage_protection_enabled", ()),
        ("set_overvoltage_protection_delay", (0.25,)),
        ("get_overvoltage_protection_delay", ()),
        ("set_overcurrent_protection_level", (1.0,)),
        ("get_overcurrent_protection_level", ()),
        ("set_overcurrent_protection_enabled", (True,)),
        ("get_overcurrent_protection_enabled", ()),
        ("set_remote_sense_enabled", (True,)),
        ("get_remote_sense_enabled", ()),
    ],
)
def test_tdk_unimplemented_optional_features_raise_from_base(
    tdk: TDKLambdaGenesys,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for TDKLambdaGenesys"):
        getattr(tdk, method_name)(*args)


# --- SimulatedPSU ---


@pytest.fixture
def sim_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.simulated.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def sim_visa(sim_visa_cls: MagicMock) -> MagicMock:
    visa = sim_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def sim(sim_visa_cls: MagicMock) -> SimulatedPSU:
    return SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET")


def test_sim_set_voltage_includes_channel_suffix(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim.set_voltage(5.0, channel=2)
    sim_visa.write.assert_called_once_with("VOLT 5.000 2")
    sim_visa.query.assert_called_once_with("SYSTEM:ERROR?")


def test_sim_get_voltage_includes_channel_suffix(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["1.234", '0,"No error"']
    assert sim.get_voltage(channel=2) == pytest.approx(1.234)
    assert sim_visa.query.call_args_list == [call("MEAS:VOLT? 2"), call("SYSTEM:ERROR?")]


def test_sim_output_enable_includes_channel_suffix(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim.output_enable(True, channel=2)
    sim_visa.write.assert_called_once_with("OUTP:STAT ON 2")
    sim.output_enable(False, channel=2)
    assert sim_visa.write.call_args_list[-1] == call("OUTP:STAT OFF 2")


def test_sim_get_output_status_parses(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["ON", '0,"No error"']
    assert sim.get_output_status(channel=1) is True
    sim_visa.query.side_effect = ["OFF", '0,"No error"']
    assert sim.get_output_status(channel=1) is False


# --- InstroPSU composition ---


def _stub_driver() -> MagicMock:
    driver = MagicMock(spec=PSUDriverBase)
    driver.get_voltage.return_value = 12.0
    driver.get_current.return_value = 0.5
    driver.get_output_status.return_value = True
    driver.get_overvoltage_protection_level.return_value = 15.0
    driver.get_overvoltage_protection_enabled.return_value = True
    driver.get_overvoltage_protection_delay.return_value = 0.25
    driver.get_overcurrent_protection_level.return_value = 2.0
    driver.get_overcurrent_protection_enabled.return_value = True
    driver.get_remote_sense_enabled.return_value = True
    return driver


def test_nominal_psu_stores_driver() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    assert psu._driver is driver


def test_nominal_psu_open_close_delegate_to_driver() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    psu.open()
    driver.open.assert_called_once()
    psu.close()
    driver.close.assert_called_once()


def test_nominal_psu_close_stops_background_before_closing_driver() -> None:
    events: list[str] = []
    driver = _stub_driver()
    driver.close.side_effect = lambda: events.append("driver.close")
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    psu.stop = MagicMock(side_effect=lambda: events.append("stop"))  # type: ignore[method-assign]

    psu.close()

    assert events == ["stop", "driver.close"]


def test_nominal_psu_set_voltage_delegates() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=2)
    psu.set_voltage(5.0, channel=2)
    driver.set_voltage.assert_called_once_with(5.0, channel=2)


def test_nominal_psu_get_voltage_returns_measurement() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    measurement = psu.get_voltage(channel=1)
    assert measurement is not None
    assert "ut.ch1.voltage" in measurement.channel_data
    assert measurement.channel_data["ut.ch1.voltage"] == [12.0]


def test_nominal_psu_get_current_returns_measurement() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    measurement = psu.get_current(channel=1)
    assert measurement is not None
    assert "ut.ch1.current" in measurement.channel_data
    assert measurement.channel_data["ut.ch1.current"] == [0.5]


def test_nominal_psu_output_enable_delegates() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    psu.output_enable(True, channel=1)
    driver.output_enable.assert_called_once_with(True, channel=1)


def test_nominal_psu_set_current_limit_delegates() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    psu.set_current_limit(1.5, channel=1)
    driver.set_current_limit.assert_called_once_with(1.5, channel=1)


def test_nominal_psu_ovp_methods_delegate_and_package() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)

    level_cmd = psu.set_overvoltage_protection_level(15.0, channel=1)
    level = psu.get_overvoltage_protection_level(channel=1)
    enabled_cmd = psu.set_overvoltage_protection_enabled(True, channel=1)
    enabled = psu.get_overvoltage_protection_enabled(channel=1)
    delay_cmd = psu.set_overvoltage_protection_delay(0.25, channel=1)
    delay = psu.get_overvoltage_protection_delay(channel=1)

    driver.set_overvoltage_protection_level.assert_called_once_with(15.0, channel=1)
    driver.get_overvoltage_protection_level.assert_called_once_with(channel=1)
    driver.set_overvoltage_protection_enabled.assert_called_once_with(True, channel=1)
    driver.get_overvoltage_protection_enabled.assert_called_once_with(channel=1)
    driver.set_overvoltage_protection_delay.assert_called_once_with(0.25, channel=1)
    driver.get_overvoltage_protection_delay.assert_called_once_with(channel=1)
    assert "ut.ch1.ovp.cmd" in level_cmd.channel_data
    assert "ut.ch1.ovp" in level.channel_data  # type: ignore[union-attr]
    assert "ut.ch1.ovp.enabled.cmd" in enabled_cmd.channel_data
    assert "ut.ch1.ovp.enabled" in enabled.channel_data  # type: ignore[union-attr]
    assert "ut.ch1.ovp.delay.cmd" in delay_cmd.channel_data
    assert "ut.ch1.ovp.delay" in delay.channel_data  # type: ignore[union-attr]


def test_nominal_psu_ocp_methods_delegate_and_package() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)

    level_cmd = psu.set_overcurrent_protection_level(2.0, channel=1)
    level = psu.get_overcurrent_protection_level(channel=1)
    enabled_cmd = psu.set_overcurrent_protection_enabled(True, channel=1)
    enabled = psu.get_overcurrent_protection_enabled(channel=1)

    driver.set_overcurrent_protection_level.assert_called_once_with(2.0, channel=1)
    driver.get_overcurrent_protection_level.assert_called_once_with(channel=1)
    driver.set_overcurrent_protection_enabled.assert_called_once_with(True, channel=1)
    driver.get_overcurrent_protection_enabled.assert_called_once_with(channel=1)
    assert "ut.ch1.ocp.cmd" in level_cmd.channel_data
    assert "ut.ch1.ocp" in level.channel_data  # type: ignore[union-attr]
    assert "ut.ch1.ocp.enabled.cmd" in enabled_cmd.channel_data
    assert "ut.ch1.ocp.enabled" in enabled.channel_data  # type: ignore[union-attr]


def test_nominal_psu_remote_sense_methods_delegate_and_package() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)

    enabled_cmd = psu.set_remote_sense_enabled(True, channel=1)
    enabled = psu.get_remote_sense_enabled(channel=1)

    driver.set_remote_sense_enabled.assert_called_once_with(True, channel=1)
    driver.get_remote_sense_enabled.assert_called_once_with(channel=1)
    assert "ut.ch1.remote_sense.cmd" in enabled_cmd.channel_data
    assert "ut.ch1.remote_sense" in enabled.channel_data  # type: ignore[union-attr]


# --- legacy_naming ---


def test_legacy_naming_publishes_old_psu_channel_names() -> None:
    """`legacy_naming=True` round-trips pre-v1.0 PSU channel names."""
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=2, legacy_naming=True)

    voltage = psu.get_voltage(channel=1)
    current = psu.get_current(channel=1)
    enabled = psu.get_output_status(channel=2)
    voltage_cmd = psu.set_voltage(5.0, channel=1)
    current_cmd = psu.set_current_limit(1.5, channel=1)
    enabled_cmd = psu.output_enable(True, channel=2)

    assert "ut.ch1_v" in voltage.channel_data  # type: ignore[union-attr]
    assert "ut.ch1_i" in current.channel_data  # type: ignore[union-attr]
    assert "ut.ch2_en" in enabled.channel_data  # type: ignore[union-attr]
    assert "ut.ch1_v.cmd" in voltage_cmd.channel_data
    assert "ut.ch1_i.cmd" in current_cmd.channel_data
    assert "ut.ch2_en.cmd" in enabled_cmd.channel_data


def test_default_naming_publishes_new_psu_channel_names() -> None:
    """Default (`legacy_naming=False`) publishes the v1.0 descriptive channel names."""
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)

    assert "ut.ch1.voltage" in psu.get_voltage(channel=1).channel_data  # type: ignore[union-attr]
    assert "ut.ch1.voltage.cmd" in psu.set_voltage(5.0, channel=1).channel_data


def test_legacy_naming_default_is_false() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    assert psu.legacy_naming is False


# --- Publish decorators: type-check invariant ---


def test_publish_command_rejects_method_returning_measurement() -> None:
    """@publish_command raises TypeError when the wrapped method returns a Measurement."""
    from instro.lib import Measurement
    from instro.lib.instrument import publish_command

    class _Bad(InstroPSU):
        @publish_command
        def bad(self) -> Measurement:  # type: ignore[override]
            return Measurement(channel_data={"ut.x": [1.0]}, timestamps=[0])

    inst = _Bad(name="ut", driver=_stub_driver(), num_channels=1)
    with pytest.raises(TypeError, match="must return Command"):
        inst.bad()


def test_publish_measurement_rejects_method_returning_command() -> None:
    """@publish_measurement raises TypeError when the wrapped method returns a Command."""
    from instro.lib import Command
    from instro.lib.instrument import publish_measurement

    class _Bad(InstroPSU):
        @publish_measurement
        def bad(self) -> Command:  # type: ignore[override]
            return Command(channel_data={"ut.x.cmd": 1.0}, timestamp=0)

    inst = _Bad(name="ut", driver=_stub_driver(), num_channels=1)
    with pytest.raises(TypeError, match="must return Measurement"):
        inst.bad()


def test_publish_measurement_passes_through_none() -> None:
    """@publish_measurement returns None without publishing when the method returns None."""
    from instro.lib.instrument import publish_measurement

    class _Quiet(InstroPSU):
        @publish_measurement
        def quiet(self) -> None:
            return None

    inst = _Quiet(name="ut", driver=_stub_driver(), num_channels=1)
    assert inst.quiet() is None


# --- Cross-driver VisaConfig pass-through (representative) ---


def test_bk_single_init_passes_prebuilt_config_to_visa_driver(bk_single_visa_cls: MagicMock) -> None:
    config = VisaConfig(
        visa_resource="ASRL19::INSTR",
        visa_backend="@py",
        serial_config=SerialConfig(baud_rate=19_200),
    )
    BK9115(config)
    bk_single_visa_cls.assert_called_once_with(config)
