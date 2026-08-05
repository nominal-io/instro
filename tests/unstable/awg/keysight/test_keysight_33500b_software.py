"""Software tests for the Keysight 33500B AWG driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import Keysight33500B
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    Triangle,
    Waveform,
)

_ARB_SAMPLES = (0.0, 0.5, 1.0, -1.0, 0.25, -0.25, 0.75, -0.75, 0.125)


@pytest.fixture
def keysight_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.awg.drivers.keysight_33500b.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def keysight_visa(keysight_visa_cls: MagicMock) -> MagicMock:
    return keysight_visa_cls.return_value


@pytest.fixture
def keysight(keysight_visa_cls: MagicMock) -> Keysight33500B:
    return Keysight33500B("TCPIP0::keysight::INSTR")


def test_01_init_builds_visa_driver_from_resource(keysight_visa_cls: MagicMock) -> None:
    Keysight33500B("TCPIP0::keysight::INSTR")

    keysight_visa_cls.assert_called_once_with("TCPIP0::keysight::INSTR")


def test_02_init_accepts_prebuilt_connection_config(keysight_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::keysight::INSTR")
    Keysight33500B(config)

    keysight_visa_cls.assert_called_once_with(config)


def test_03_open_close_delegate_to_visa(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight.open()
    keysight.close()

    keysight_visa.open.assert_called_once()
    keysight_visa.close.assert_called_once()


@pytest.mark.parametrize("response", ['0,"No error"', '+0,"No error"'])
def test_04_check_errors_accepts_zero_codes(keysight: Keysight33500B, keysight_visa: MagicMock, response: str) -> None:
    keysight_visa.query.return_value = response

    keysight.check_errors()

    keysight_visa.query.assert_called_once_with(":SYST:ERR?")


def test_05_check_errors_raises_on_nonzero_code(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '-113,"Undefined header"'

    with pytest.raises(RuntimeError, match=r'Keysight 33500B reported error: -113,"Undefined header"'):
        keysight.check_errors()


def test_06_set_waveform_sine_writes_function_frequency_phase(
    keysight: Keysight33500B,
    keysight_visa: MagicMock,
) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, Sine(frequency_hz=1000.0, phase_deg=90.0))

    assert keysight_visa.write.call_args_list == [
        call("FUNC SIN"),
        call("FREQ 1000.0"),
        call("PHAS 90.0"),
    ]


def test_07_set_waveform_wraps_negative_phase(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, Sine(frequency_hz=1000.0, phase_deg=-90.0))

    assert call("PHAS 270.0") in keysight_visa.write.call_args_list


def test_08_set_waveform_square_writes_duty_cycle(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, Square(frequency_hz=500.0, duty_cycle_pct=25.0))

    assert keysight_visa.write.call_args_list == [
        call("FUNC SQU"),
        call("FREQ 500.0"),
        call("PHAS 0.0"),
        call("FUNC:SQU:DCYC 25.0"),
    ]


@pytest.mark.parametrize(
    ("waveform", "expected_writes"),
    [
        (
            Sawtooth(frequency_hz=100.0),
            [call("FUNC RAMP"), call("FREQ 100.0"), call("PHAS 0.0"), call("FUNC:RAMP:SYMM 100")],
        ),
        (
            Triangle(frequency_hz=100.0),
            [call("FUNC TRI"), call("FREQ 100.0"), call("PHAS 0.0")],
        ),
    ],
    ids=["sawtooth", "triangle"],
)
def test_09_set_waveform_ramp_shapes_dispatch_correctly(
    keysight: Keysight33500B,
    keysight_visa: MagicMock,
    waveform: Waveform,
    expected_writes: list,
) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, waveform)

    assert keysight_visa.write.call_args_list == expected_writes


def test_10_set_waveform_pulse_writes_width(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, Pulse(frequency_hz=1000.0, width_s=0.0002))

    assert keysight_visa.write.call_args_list == [
        call("FUNC PULS"),
        call("FREQ 1000.0"),
        call("FUNC:PULS:WIDT 0.0002"),
    ]


def test_11_set_waveform_pulse_rejects_nonzero_delay(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="cannot program a pulse delay"):
        keysight.set_waveform(1, Pulse(frequency_hz=1000.0, width_s=0.0002, delay_s=0.0001))

    keysight_visa.write.assert_not_called()


def test_12_set_waveform_arbitrary_downloads_normalized_samples(
    keysight: Keysight33500B,
    keysight_visa: MagicMock,
) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=1000000.0))

    assert keysight_visa.write.call_args_list == [
        call("DATA:ARB INSTRO_ARB, 0.0,0.5,1.0,-1.0,0.25,-0.25,0.75,-0.75,0.125"),
        call("FUNC:ARB:SRAT 1000000.0"),
        call("FUNC:ARB INSTRO_ARB"),
        call("FUNC ARB"),
    ]


@pytest.mark.parametrize("num_points", [2, 65537], ids=["too_few", "too_many"])
def test_13_set_waveform_arbitrary_rejects_bad_point_counts(
    keysight: Keysight33500B,
    keysight_visa: MagicMock,
    num_points: int,
) -> None:
    waveform = Arbitrary(samples=(0.0,) * num_points, sample_rate_hz=1000000.0)

    with pytest.raises(ValueError, match="8 to 65536 arbitrary points"):
        keysight.set_waveform(1, waveform)

    keysight_visa.write.assert_not_called()


def test_14_set_waveform_static_value_writes_dc_offset(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight.set_waveform(1, StaticValue(value=1.5))

    assert keysight_visa.write.call_args_list == [
        call("FUNC DC"),
        call("VOLT:OFFS 1.5"),
    ]


def test_15_set_waveform_rejects_invalid_channel(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="only supports 1 channel"):
        keysight.set_waveform(2, Sine(frequency_hz=1000.0))

    keysight_visa.write.assert_not_called()


def test_16_get_waveform_parses_sine(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.side_effect = ["SIN", "1.000000E+03", "9.000000E+01"]

    waveform = keysight.get_waveform(1)

    assert keysight_visa.query.call_args_list == [call("FUNC?"), call("FREQ?"), call("PHAS?")]
    assert waveform == Sine(frequency_hz=1000.0, phase_deg=90.0)


def test_17_get_waveform_parses_square_duty_cycle(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.side_effect = ["SQU", "5.000000E+02", "0.000000E+00", "2.500000E+01"]

    waveform = keysight.get_waveform(1)

    assert keysight_visa.query.call_args_list == [
        call("FUNC?"),
        call("FREQ?"),
        call("PHAS?"),
        call("FUNC:SQU:DCYC?"),
    ]
    assert waveform == Square(frequency_hz=500.0, duty_cycle_pct=25.0, phase_deg=0.0)


@pytest.mark.parametrize(
    ("function_reply", "expected"),
    [
        ("RAMP", Sawtooth(frequency_hz=100.0)),
        ("TRI", Triangle(frequency_hz=100.0)),
    ],
    ids=["sawtooth", "triangle"],
)
def test_18_get_waveform_distinguishes_ramp_and_triangle(
    keysight: Keysight33500B,
    keysight_visa: MagicMock,
    function_reply: str,
    expected: Waveform,
) -> None:
    keysight_visa.query.side_effect = [function_reply, "1.000000E+02", "0.000000E+00"]

    waveform = keysight.get_waveform(1)

    assert waveform == expected


def test_19_get_waveform_parses_pulse_width(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.side_effect = ["PULS", "1.000000E+03", "2.000000E-04"]

    waveform = keysight.get_waveform(1)

    assert keysight_visa.query.call_args_list == [call("FUNC?"), call("FREQ?"), call("FUNC:PULS:WIDT?")]
    assert waveform == Pulse(frequency_hz=1000.0, width_s=0.0002)


def test_20_get_waveform_parses_static_value(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.side_effect = ["DC", "1.500000E+00"]

    waveform = keysight.get_waveform(1)

    assert keysight_visa.query.call_args_list == [call("FUNC?"), call("VOLT:OFFS?")]
    assert waveform == StaticValue(value=1.5)


def test_21_get_waveform_returns_cached_arbitrary(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    arbitrary = Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=1000000.0)
    keysight_visa.query.return_value = '0,"No error"'
    keysight.set_waveform(1, arbitrary)
    keysight_visa.query.side_effect = ["ARB"]

    assert keysight.get_waveform(1) is arbitrary


def test_22_get_waveform_unprogrammed_arbitrary_raises(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = "ARB"

    with pytest.raises(RuntimeError, match="not programmed by this driver"):
        keysight.get_waveform(1)


def test_23_get_waveform_unknown_shape_raises(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = "NOIS"

    with pytest.raises(ValueError, match="unsupported waveform 'NOIS'"):
        keysight.get_waveform(1)


def test_24_set_amplitude_writes_unit_then_value(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VPP)

    assert keysight_visa.write.call_args_list == [
        call("VOLT:UNIT VPP"),
        call("VOLT 2.5"),
    ]


def test_25_set_amplitude_rejects_vp_unit(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="VP is not supported"):
        keysight.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VP)

    keysight_visa.write.assert_not_called()


def test_26_get_amplitude_parses_value_and_unit(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.side_effect = ["1.000000E+00", "VRMS\n"]

    amplitude, unit = keysight.get_amplitude(1)

    assert keysight_visa.query.call_args_list == [call("VOLT?"), call("VOLT:UNIT?")]
    assert amplitude == pytest.approx(1.0)
    assert unit is AmplitudeMeasurementUnit.VRMS


def test_27_offset_roundtrip_uses_offset_commands(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight.set_offset(1, 0.5)
    keysight_visa.write.assert_called_once_with("VOLT:OFFS 0.5")

    keysight_visa.query.return_value = "5.000000E-01"
    assert keysight.get_offset(1) == pytest.approx(0.5)
    keysight_visa.query.assert_called_once_with("VOLT:OFFS?")


def test_28_output_enable_formats_on_off_and_parses_state(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight.output_enable(1, True)
    keysight.output_enable(1, False)
    assert keysight_visa.write.call_args_list == [call("OUTP ON"), call("OUTP OFF")]

    keysight_visa.query.return_value = "1"
    assert keysight.get_output_state(1) is True
    keysight_visa.query.assert_called_once_with("OUTP?")

    keysight_visa.query.return_value = "0"
    assert keysight.get_output_state(1) is False


def test_29_output_load_roundtrip_and_high_z(keysight: Keysight33500B, keysight_visa: MagicMock) -> None:
    keysight.set_output_load(1, 50.0)
    keysight.set_output_load(1, None)
    assert keysight_visa.write.call_args_list == [call("OUTP:LOAD 50.0"), call("OUTP:LOAD INF")]

    keysight_visa.query.return_value = "5.000000E+01"
    assert keysight.get_output_load(1) == pytest.approx(50.0)
    keysight_visa.query.assert_called_once_with("OUTP:LOAD?")

    keysight_visa.query.return_value = "9.900000E+37"
    assert keysight.get_output_load(1) is None
