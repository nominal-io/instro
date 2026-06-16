"""Software tests for the B&K Precision 9115-series PSU driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import SerialConfig, VisaConfig
from instro.psu.drivers import BK9115


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


def test_bk_single_init_passes_serial_config_to_visa_driver(bk_single_visa_cls: MagicMock) -> None:
    config = VisaConfig(
        visa_resource="ASRL19::INSTR",
        serial_config=SerialConfig(baud_rate=19_200),
    )
    BK9115(config)
    bk_single_visa_cls.assert_called_once_with(config)


def test_bk_single_open_close_delegate_to_visa(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single.open()
    bk_single_visa.open.assert_called_once()
    bk_single.close()
    bk_single_visa.close.assert_called_once()


def test_bk_single_set_voltage_writes_checked(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single.set_voltage(5.0, channel=1)
    bk_single_visa.write.assert_called_once_with("VOLT 5.000")
    bk_single_visa.query.assert_called_once_with("SYST:ERR?")


def test_bk_single_get_voltage_parses_response(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single_visa.query.side_effect = ["12.345", '0,"No error"']
    assert bk_single.get_voltage(channel=1) == pytest.approx(12.345)
    assert bk_single_visa.query.call_args_list == [call("MEAS:VOLT?"), call("SYST:ERR?")]


def test_bk_single_set_current_limit_writes_checked(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single.set_current_limit(1.25, channel=1)
    bk_single_visa.write.assert_called_once_with("CURR 1.250")
    bk_single_visa.query.assert_called_once_with("SYST:ERR?")


def test_bk_single_get_current_parses_response(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single_visa.query.side_effect = ["0.500", '0,"No error"']
    assert bk_single.get_current(channel=1) == pytest.approx(0.5)


def test_bk_single_output_enable_writes_checked(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single.output_enable(True, channel=1)
    bk_single_visa.write.assert_called_once_with("OUTP:STAT ON")
    bk_single.output_enable(False, channel=1)
    assert bk_single_visa.write.call_args_list[-1] == call("OUTP:STAT OFF")


def test_bk_single_get_output_status_parses(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single_visa.query.side_effect = ["1", '0,"No error"']
    assert bk_single.get_output_status(channel=1) is True
    bk_single_visa.query.side_effect = ["0", '0,"No error"']
    assert bk_single.get_output_status(channel=1) is False


def test_bk_single_set_overvoltage_protection_level_writes_checked(
    bk_single: BK9115,
    bk_single_visa: MagicMock,
) -> None:
    bk_single.set_overvoltage_protection_level(12.5, channel=1)
    bk_single_visa.write.assert_called_once_with("VOLT:PROT 12.500")
    bk_single_visa.query.assert_called_once_with("SYST:ERR?")


def test_bk_single_get_overvoltage_protection_level_parses_response(
    bk_single: BK9115,
    bk_single_visa: MagicMock,
) -> None:
    bk_single_visa.query.side_effect = ["12.500", '0,"No error"']
    assert bk_single.get_overvoltage_protection_level(channel=1) == pytest.approx(12.5)
    assert bk_single_visa.query.call_args_list == [call("VOLT:PROT?"), call("SYST:ERR?")]


def test_bk_single_set_overvoltage_protection_enabled_writes_state(
    bk_single: BK9115,
    bk_single_visa: MagicMock,
) -> None:
    bk_single.set_overvoltage_protection_enabled(True, channel=1)
    bk_single.set_overvoltage_protection_enabled(False, channel=1)
    assert bk_single_visa.write.call_args_list == [call("VOLT:PROT:STAT ON"), call("VOLT:PROT:STAT OFF")]


def test_bk_single_get_overvoltage_protection_enabled_parses_state(
    bk_single: BK9115,
    bk_single_visa: MagicMock,
) -> None:
    bk_single_visa.query.side_effect = ["1", '0,"No error"']
    assert bk_single.get_overvoltage_protection_enabled(channel=1) is True
    bk_single_visa.query.side_effect = ["0", '0,"No error"']
    assert bk_single.get_overvoltage_protection_enabled(channel=1) is False


def test_bk_single_set_overvoltage_protection_delay_writes_checked(
    bk_single: BK9115,
    bk_single_visa: MagicMock,
) -> None:
    bk_single.set_overvoltage_protection_delay(0.25, channel=1)
    bk_single_visa.write.assert_called_once_with("VOLT:PROT:DEL 0.250")
    bk_single_visa.query.assert_called_once_with("SYST:ERR?")


def test_bk_single_get_overvoltage_protection_delay_parses_response(
    bk_single: BK9115,
    bk_single_visa: MagicMock,
) -> None:
    bk_single_visa.query.side_effect = ["0.250", '0,"No error"']
    assert bk_single.get_overvoltage_protection_delay(channel=1) == pytest.approx(0.25)
    assert bk_single_visa.query.call_args_list == [call("VOLT:PROT:DEL?"), call("SYST:ERR?")]


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_voltage", (5.0,)),
        ("get_voltage", ()),
        ("set_current_limit", (1.25,)),
        ("get_current", ()),
        ("output_enable", (True,)),
        ("get_output_status", ()),
        ("set_overvoltage_protection_level", (12.5,)),
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
def test_bk_single_invalid_channel_raises_without_scpi(
    bk_single: BK9115,
    bk_single_visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(ValueError, match="BK 9115 channel must be 1"):
        getattr(bk_single, method_name)(*args, channel=2)

    bk_single_visa.write.assert_not_called()
    bk_single_visa.query.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overcurrent_protection_level", (1.0,)),
        ("get_overcurrent_protection_level", ()),
        ("set_overcurrent_protection_enabled", (True,)),
        ("get_overcurrent_protection_enabled", ()),
    ],
)
def test_bk_single_overcurrent_protection_methods_raise_unsupported(
    bk_single: BK9115,
    bk_single_visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match=f"{method_name} is not supported by the B&K Precision 9115-series PSU",
    ):
        getattr(bk_single, method_name)(*args, channel=1)

    bk_single_visa.write.assert_not_called()
    bk_single_visa.query.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_remote_sense_enabled", (True,)),
        ("get_remote_sense_enabled", ()),
    ],
)
def test_bk_single_remote_sense_methods_raise_unsupported(
    bk_single: BK9115,
    bk_single_visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match=f"{method_name} is not supported by the B&K Precision 9115-series PSU",
    ):
        getattr(bk_single, method_name)(*args, channel=1)

    bk_single_visa.write.assert_not_called()
    bk_single_visa.query.assert_not_called()


def test_bk_single_check_errors_raises_on_nonzero(bk_single: BK9115, bk_single_visa: MagicMock) -> None:
    bk_single_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        bk_single.set_voltage(1.0, channel=1)
