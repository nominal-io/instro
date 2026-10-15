"""Software tests for the GW Instek GPD-3303 PSU driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import TerminatorConfig, VisaConfig
from instro.psu.drivers.gw_instek_gpd3303 import GWInstekGPD3303
from instro.psu.types import OperatingMode

STATUS_OUTPUT_OFF = "0 0 0 1 1 X 0 X"
STATUS_OUTPUT_ON_CH1_CV_CH2_CC = "1 0 0 1 1 X 1 X"
STATUS_OUTPUT_ON_CH1_CC_CH2_CV = "0 1 0 1 1 X 1 X"


@pytest.fixture
def gpd_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.gw_instek_gpd3303.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def gpd_visa(gpd_visa_cls: MagicMock) -> MagicMock:
    visa = gpd_visa_cls.return_value
    visa.query.return_value = "No Error."
    return visa


@pytest.fixture
def gpd(gpd_visa_cls: MagicMock) -> GWInstekGPD3303:
    return GWInstekGPD3303("ASRL3::INSTR")


def test_gpd_init_applies_device_terminators_to_resource_string(gpd_visa_cls: MagicMock) -> None:
    GWInstekGPD3303("ASRL3::INSTR")

    config = gpd_visa_cls.call_args.args[0]
    assert config.visa_resource == "ASRL3::INSTR"
    assert (config.terminator.read, config.terminator.write) == ("\r", "\r\n")


def test_gpd_init_keeps_caller_config_but_forces_device_terminators(gpd_visa_cls: MagicMock) -> None:
    GWInstekGPD3303(VisaConfig("ASRL3::INSTR", visa_backend="@py", terminator=TerminatorConfig(read="\n", write="\n")))

    config = gpd_visa_cls.call_args.args[0]
    assert config.visa_backend == "@py"
    assert (config.terminator.read, config.terminator.write) == ("\r", "\r\n")


def test_gpd_open_close_delegate_to_visa(gpd: GWInstekGPD3303, gpd_visa: MagicMock) -> None:
    gpd.open()
    gpd.close()

    gpd_visa.open.assert_called_once()
    gpd_visa.close.assert_called_once()


def test_gpd_setters_write_per_channel_at_gpd3303d_resolution_then_check_errors(
    gpd: GWInstekGPD3303, gpd_visa: MagicMock
) -> None:
    gpd.set_voltage(20.345, channel=2)
    gpd.set_current_limit(2.234, channel=1)

    assert gpd_visa.write.call_args_list == [call("VSET2:20.3"), call("ISET1:2.23")]
    assert gpd_visa.query.call_args_list == [call("ERR?"), call("ERR?")]


def test_gpd_write_raises_on_device_error(gpd: GWInstekGPD3303, gpd_visa: MagicMock) -> None:
    gpd_visa.query.return_value = "Data out of range."

    with pytest.raises(RuntimeError, match="Data out of range"):
        gpd.set_voltage(40.0, channel=1)


@pytest.mark.parametrize(
    ("method", "command", "response", "expected"),
    [
        ("get_voltage", "VOUT1?", "1.1V", 1.1),
        ("get_current", "IOUT1?", "0.00A", 0.0),
        ("get_voltage_setpoint", "VSET1?", "24.0V", 24.0),
        ("get_current_setpoint", "ISET1?", "0.12A", 0.12),
    ],
)
def test_gpd_getters_query_and_strip_unit_suffix(
    gpd: GWInstekGPD3303, gpd_visa: MagicMock, method: str, command: str, response: str, expected: float
) -> None:
    gpd_visa.query.return_value = response

    assert getattr(gpd, method)(channel=1) == pytest.approx(expected)
    gpd_visa.query.assert_called_once_with(command)


def test_gpd_output_enable_is_global(gpd: GWInstekGPD3303, gpd_visa: MagicMock) -> None:
    gpd.output_enable(True, channel=2)
    gpd.output_enable(False, channel=1)

    assert gpd_visa.write.call_args_list == [call("OUT1"), call("OUT0")]


@pytest.mark.parametrize(
    ("response", "expected"),
    [(STATUS_OUTPUT_ON_CH1_CV_CH2_CC, True), (STATUS_OUTPUT_OFF, False)],
)
def test_gpd_output_status_reads_bit6_and_drains_legend_lines(
    gpd: GWInstekGPD3303, gpd_visa: MagicMock, response: str, expected: bool
) -> None:
    gpd_visa.query.return_value = response

    assert gpd.get_output_status(channel=1) is expected
    gpd_visa.query.assert_called_once_with("STATUS?")
    assert gpd_visa.read.call_count == 2


@pytest.mark.parametrize("reply", ["", "garbage", "1 1 1", "No Error.", "x x x x x X x X", "0 0 0 1 1 X 2 X"])
def test_gpd_status_rejects_malformed_reply_instead_of_reporting_output_off(
    gpd: GWInstekGPD3303, gpd_visa: MagicMock, reply: str
) -> None:
    gpd_visa.query.return_value = reply

    with pytest.raises(RuntimeError, match="unexpected STATUS"):
        gpd.get_output_status(channel=1)


def test_gpd_operating_mode_uses_per_channel_cv_bit_when_output_on(gpd: GWInstekGPD3303, gpd_visa: MagicMock) -> None:
    gpd_visa.query.return_value = STATUS_OUTPUT_ON_CH1_CC_CH2_CV

    assert gpd.get_operating_mode(channel=1) is OperatingMode.CONSTANT_CURRENT
    assert gpd.get_operating_mode(channel=2) is OperatingMode.CONSTANT_VOLTAGE


def test_gpd_operating_mode_is_off_when_output_off(gpd: GWInstekGPD3303, gpd_visa: MagicMock) -> None:
    gpd_visa.query.return_value = STATUS_OUTPUT_OFF

    assert gpd.get_operating_mode(channel=1) is OperatingMode.OFF


def test_gpd_channel_3_is_not_supported_and_bad_channel_raises(gpd: GWInstekGPD3303, gpd_visa: MagicMock) -> None:
    with pytest.raises(FeatureNotSupportedError, match="channel 3"):
        gpd.set_voltage(5.0, channel=3)
    with pytest.raises(ValueError, match="channel must be 1, 2, or 3"):
        gpd.get_voltage(channel=4)

    gpd_visa.write.assert_not_called()
    gpd_visa.query.assert_not_called()


def test_gpd_protection_methods_are_not_supported(gpd: GWInstekGPD3303) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_level"):
        gpd.set_overvoltage_protection_level(10.0, channel=1)
