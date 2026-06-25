"""Software tests for the B&K Precision 914X-series PSU driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import TerminatorConfig, VisaConfig
from instro.psu.drivers.bk_914x import BK914X


@pytest.fixture
def bk_multi_visa_cls() -> Iterator[MagicMock]:
    with (
        patch("instro.psu.drivers.bk_914x.VisaDriver", autospec=True) as cls,
        patch("instro.psu.drivers.bk_914x.time.sleep", autospec=True),
    ):
        yield cls


@pytest.fixture
def bk_multi_visa(bk_multi_visa_cls: MagicMock) -> MagicMock:
    visa = bk_multi_visa_cls.return_value
    visa.read_raw.return_value = b'0,"No error"'
    return visa


@pytest.fixture
def bk_multi(bk_multi_visa_cls: MagicMock) -> BK914X:
    return BK914X("USB0::0xFFFF::0x9140::SN::INSTR")


def test_bk_multi_init_builds_visa_driver_from_resource(bk_multi_visa_cls: MagicMock) -> None:
    BK914X("USB0::0xFFFF::0x9140::SN::INSTR")
    bk_multi_visa_cls.assert_called_once_with(
        VisaConfig(
            visa_resource="USB0::0xFFFF::0x9140::SN::INSTR",
            terminator=TerminatorConfig(read="\n", write="\n"),
        )
    )


def test_bk_multi_init_forces_lf_terminator_on_connection_config(bk_multi_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="USB0::example::INSTR", terminator=TerminatorConfig(read="\r\n", write="\r\n"))
    BK914X(config)
    bk_multi_visa_cls.assert_called_once_with(
        VisaConfig(visa_resource="USB0::example::INSTR", terminator=TerminatorConfig(read="\n", write="\n"))
    )


def test_bk_multi_set_voltage_selects_channel_then_writes(bk_multi: BK914X, bk_multi_visa: MagicMock) -> None:
    bk_multi.set_voltage(3.3, channel=2)
    assert bk_multi_visa.write.call_args_list == [call("INST 1"), call("VOLT 3.300"), call("SYST:ERR?")]
    bk_multi_visa.read_raw.assert_called_once_with()


def test_bk_multi_selects_channel_one_before_first_write(bk_multi: BK914X, bk_multi_visa: MagicMock) -> None:
    bk_multi.set_voltage(3.3, channel=1)
    assert bk_multi_visa.write.call_args_list == [call("INST 0"), call("VOLT 3.300"), call("SYST:ERR?")]


def test_bk_multi_skips_channel_select_when_active(bk_multi: BK914X, bk_multi_visa: MagicMock) -> None:
    bk_multi.set_voltage(3.3, channel=1)
    bk_multi.set_current_limit(0.5, channel=1)
    assert bk_multi_visa.write.call_args_list == [
        call("INST 0"),
        call("VOLT 3.300"),
        call("SYST:ERR?"),
        call("CURR 0.500"),
        call("SYST:ERR?"),
    ]


def test_bk_multi_get_voltage_returns_float(bk_multi: BK914X, bk_multi_visa: MagicMock) -> None:
    bk_multi_visa.read_raw.side_effect = [b"7.890", b'0,"No error"']
    assert bk_multi.get_voltage(channel=2) == pytest.approx(7.89)
    assert bk_multi_visa.write.call_args_list == [call("INST 1"), call("MEAS:VOLT?"), call("SYST:ERR?")]
    assert bk_multi_visa.read_raw.call_args_list == [call(), call()]


def test_bk_multi_get_output_status_parses(bk_multi: BK914X, bk_multi_visa: MagicMock) -> None:
    bk_multi_visa.read_raw.side_effect = [b"1", b'0,"No error"']
    assert bk_multi.get_output_status(channel=1) is True


def test_bk_multi_check_errors_raises_on_nonzero(bk_multi: BK914X, bk_multi_visa: MagicMock) -> None:
    bk_multi_visa.read_raw.return_value = b'-100,"Command error"'
    with pytest.raises(RuntimeError, match="BK914X PSU reported error"):
        bk_multi.set_voltage(1.0, channel=1)


def test_bk_multi_set_overvoltage_protection_level_selects_channel_then_writes(
    bk_multi: BK914X,
    bk_multi_visa: MagicMock,
) -> None:
    bk_multi.set_overvoltage_protection_level(12.5, channel=3)
    assert bk_multi_visa.write.call_args_list == [call("INST 2"), call("VOLT:PROT 12.500"), call("SYST:ERR?")]
    bk_multi_visa.read_raw.assert_called_once_with()


def test_bk_multi_get_overvoltage_protection_level_selects_channel_then_queries(
    bk_multi: BK914X,
    bk_multi_visa: MagicMock,
) -> None:
    bk_multi_visa.read_raw.side_effect = [b"12.500", b'0,"No error"']
    assert bk_multi.get_overvoltage_protection_level(channel=2) == pytest.approx(12.5)
    assert bk_multi_visa.write.call_args_list == [call("INST 1"), call("VOLT:PROT?"), call("SYST:ERR?")]
    assert bk_multi_visa.read_raw.call_args_list == [call(), call()]


def test_bk_multi_set_overvoltage_protection_enabled_raises_unsupported(bk_multi: BK914X) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_enabled is not supported by the B&K Precision 914X-series PSU",
    ):
        bk_multi.set_overvoltage_protection_enabled(False, channel=1)


def test_bk_multi_get_overvoltage_protection_enabled_raises_unsupported(bk_multi: BK914X) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_enabled is not supported by the B&K Precision 914X-series PSU",
    ):
        bk_multi.get_overvoltage_protection_enabled(channel=1)


def test_bk_multi_set_overvoltage_protection_delay_raises_unsupported(bk_multi: BK914X) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_delay is not supported by the B&K Precision 914X-series PSU",
    ):
        bk_multi.set_overvoltage_protection_delay(0.25, channel=1)


def test_bk_multi_get_overvoltage_protection_delay_raises_unsupported(bk_multi: BK914X) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_delay is not supported by the B&K Precision 914X-series PSU",
    ):
        bk_multi.get_overvoltage_protection_delay(channel=1)


def test_bk_multi_set_overcurrent_protection_level_selects_channel_then_writes(
    bk_multi: BK914X,
    bk_multi_visa: MagicMock,
) -> None:
    bk_multi.set_overcurrent_protection_level(1.25, channel=2)
    assert bk_multi_visa.write.call_args_list == [call("INST 1"), call("CURR:PROT 1.250"), call("SYST:ERR?")]
    bk_multi_visa.read_raw.assert_called_once_with()


def test_bk_multi_get_overcurrent_protection_level_selects_channel_then_queries(
    bk_multi: BK914X,
    bk_multi_visa: MagicMock,
) -> None:
    bk_multi_visa.read_raw.side_effect = [b"1.250", b'0,"No error"']
    assert bk_multi.get_overcurrent_protection_level(channel=2) == pytest.approx(1.25)
    assert bk_multi_visa.write.call_args_list == [call("INST 1"), call("CURR:PROT?"), call("SYST:ERR?")]
    assert bk_multi_visa.read_raw.call_args_list == [call(), call()]


def test_bk_multi_set_overcurrent_protection_enabled_selects_channel_then_writes(
    bk_multi: BK914X,
    bk_multi_visa: MagicMock,
) -> None:
    bk_multi.set_overcurrent_protection_enabled(True, channel=2)
    bk_multi.set_overcurrent_protection_enabled(False, channel=2)
    assert bk_multi_visa.write.call_args_list == [
        call("INST 1"),
        call("CURR:PROT:STAT ON"),
        call("SYST:ERR?"),
        call("CURR:PROT:STAT OFF"),
        call("SYST:ERR?"),
    ]


def test_bk_multi_get_overcurrent_protection_enabled_selects_channel_then_parses(
    bk_multi: BK914X,
    bk_multi_visa: MagicMock,
) -> None:
    bk_multi_visa.read_raw.side_effect = [b"1", b'0,"No error"', b"0", b'0,"No error"']
    assert bk_multi.get_overcurrent_protection_enabled(channel=2) is True
    assert bk_multi.get_overcurrent_protection_enabled(channel=2) is False
    assert bk_multi_visa.write.call_args_list == [
        call("INST 1"),
        call("CURR:PROT:STAT?"),
        call("SYST:ERR?"),
        call("CURR:PROT:STAT?"),
        call("SYST:ERR?"),
    ]
    assert bk_multi_visa.read_raw.call_args_list == [call(), call(), call(), call()]


def test_bk_multi_set_remote_sense_enabled_selects_channel_then_writes(
    bk_multi: BK914X,
    bk_multi_visa: MagicMock,
) -> None:
    bk_multi.set_remote_sense_enabled(True, channel=2)
    bk_multi.set_remote_sense_enabled(False, channel=2)
    assert bk_multi_visa.write.call_args_list == [
        call("INST 1"),
        call("VOLT:SENS ON"),
        call("SYST:ERR?"),
        call("VOLT:SENS OFF"),
        call("SYST:ERR?"),
    ]


def test_bk_multi_get_remote_sense_enabled_selects_channel_then_parses(
    bk_multi: BK914X,
    bk_multi_visa: MagicMock,
) -> None:
    bk_multi_visa.read_raw.side_effect = [b"1", b'0,"No error"', b"0", b'0,"No error"']
    assert bk_multi.get_remote_sense_enabled(channel=2) is True
    assert bk_multi.get_remote_sense_enabled(channel=2) is False
    assert bk_multi_visa.write.call_args_list == [
        call("INST 1"),
        call("VOLT:SENS?"),
        call("SYST:ERR?"),
        call("VOLT:SENS?"),
        call("SYST:ERR?"),
    ]
    assert bk_multi_visa.read_raw.call_args_list == [call(), call(), call(), call()]
