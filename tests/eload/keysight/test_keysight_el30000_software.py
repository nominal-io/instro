"""Wire-level tests for KeysightEL30000 over a mocked VisaDriver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.eload.drivers.keysight_el30000 import KeysightEL30000
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports import VisaConfig

ERR_QUERY = call("SYST:ERR?")


@pytest.fixture
def visa_driver_cls() -> Iterator[MagicMock]:
    with patch("instro.eload.drivers.keysight_el30000.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def visa_mock(visa_driver_cls: MagicMock) -> MagicMock:
    visa = visa_driver_cls.return_value
    visa.query.return_value = '+0,"No error"'
    return visa


@pytest.fixture
def eload(visa_driver_cls: MagicMock) -> KeysightEL30000:
    return KeysightEL30000("TCPIP::1.2.3.4::INSTR")


def test_init_builds_visa_driver_from_resource_or_config(visa_driver_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP::1.2.3.4::INSTR")
    KeysightEL30000("TCPIP::1.2.3.4::INSTR")
    KeysightEL30000(config)

    assert visa_driver_cls.call_args_list == [call("TCPIP::1.2.3.4::INSTR"), call(config)]


def test_open_clears_stale_errors_then_takes_remote(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    eload.open()

    visa_mock.open.assert_called_once()
    assert visa_mock.write.call_args_list == [call("*CLS"), call("SYST:REM")]


def test_open_closes_visa_when_setup_fails(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    visa_mock.query.return_value = '-200,"Execution error"'

    with pytest.raises(RuntimeError, match="reported error"):
        eload.open()

    visa_mock.close.assert_called_once()


def test_set_mode_and_level_address_the_channel(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    eload.set_mode(LoadMode.CR, channel=2)
    eload.set_level(LoadMode.CP, 12.5, channel=2, curr_limit=None)

    assert visa_mock.write.call_args_list == [call("FUNC RESistance, (@2)"), call("POWer 12.5, (@2)")]


def test_cv_level_applies_current_limit_before_voltage_and_only_in_cv(
    eload: KeysightEL30000, visa_mock: MagicMock
) -> None:
    eload.set_level(LoadMode.CV, 5.0, channel=1, curr_limit=2.0)
    eload.set_level(LoadMode.CC, 1.0, channel=1, curr_limit=2.0)

    assert visa_mock.write.call_args_list == [
        call("CURR:LIM 2.0, (@1)"),
        call("VOLTage 5.0, (@1)"),
        call("CURRent 1.0, (@1)"),
    ]


def test_set_range_writes_range_for_every_mode(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    eload.set_range(LoadMode.CP, 30.0, channel=1)

    visa_mock.write.assert_called_once_with("POWer:RANG 30.0, (@1)")


@pytest.mark.parametrize(
    "direction,expected",
    [
        (SlewRateDirection.RISE, ["CURR:SLEW 2000000.0, (@1)"]),
        (SlewRateDirection.FALL, ["CURR:SLEW:NEG 2000000.0, (@1)"]),
        (SlewRateDirection.BOTH, ["CURR:SLEW 2000000.0, (@1)", "CURR:SLEW:NEG 2000000.0, (@1)"]),
    ],
)
def test_set_slewrate_converts_a_per_us_to_a_per_s(
    eload: KeysightEL30000, visa_mock: MagicMock, direction: SlewRateDirection, expected: list[str]
) -> None:
    eload.set_slewrate(direction, 2.0, channel=1)

    assert visa_mock.write.call_args_list == [call(c) for c in expected]


def test_output_enable_writes_channel_list(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    eload.output_enable(True, channel=2)

    visa_mock.write.assert_called_once_with("INP 1, (@2)")


def test_short_output_selects_channel_first_under_lock(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    eload.short_output(True, channel=2)

    assert visa_mock.write.call_args_list == [call("INST:NSEL 2"), call("INP:SHOR 1")]
    visa_mock.lock.assert_called()


def test_short_output_is_withheld_when_channel_selection_fails(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    visa_mock.query.return_value = '-222,"Data out of range"'

    with pytest.raises(RuntimeError, match="reported error"):
        eload.short_output(True, channel=3)

    visa_mock.write.assert_called_once_with("INST:NSEL 3")


def test_measurements_put_a_space_before_the_channel_list(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    visa_mock.query.side_effect = ["+1.500000E+00", '+0,"No error"', "+1.200000E+01", '+0,"No error"']

    assert eload.get_current(channel=1) == 1.5
    assert eload.get_voltage(channel=2) == 12.0
    assert [c.args[0] for c in visa_mock.query.call_args_list if c != ERR_QUERY] == [
        "MEAS:CURR? (@1)",
        "MEAS:VOLT? (@2)",
    ]


def test_nonzero_error_raises(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    visa_mock.query.return_value = '-222,"Data out of range"'

    with pytest.raises(RuntimeError, match="KeysightEL30000 reported error"):
        eload.output_enable(True, channel=1)


def test_set_range_out_of_range_error_points_at_the_present_level(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    visa_mock.query.return_value = '-222,"Data out of range"'

    with pytest.raises(RuntimeError, match=r"CR range 1250.0.*channel 1's present CR level"):
        eload.set_range(LoadMode.CR, 1250.0, channel=1)


def test_set_range_passes_other_errors_through(eload: KeysightEL30000, visa_mock: MagicMock) -> None:
    visa_mock.query.return_value = '-108,"Parameter not allowed"'

    with pytest.raises(RuntimeError, match="-108"):
        eload.set_range(LoadMode.CR, 1250.0, channel=1)
