"""Software tests for the Rigol DG1022Z AWG driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    Triangle,
)


@pytest.fixture
def dg_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.awg.drivers.rigol_dg1022z.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def dg_visa(dg_visa_cls: MagicMock) -> MagicMock:
    visa = dg_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def dg(dg_visa_cls: MagicMock) -> RigolDG1022Z:
    return RigolDG1022Z("USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR")


# --- Init and lifecycle ---


def test_dg1022z_init_builds_visa_driver_from_resource(dg_visa_cls: MagicMock) -> None:
    RigolDG1022Z("USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR")
    dg_visa_cls.assert_called_once_with("USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR")


def test_dg1022z_init_accepts_prebuilt_connection_config(dg_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::192.168.1.100::INSTR")
    RigolDG1022Z(config)
    dg_visa_cls.assert_called_once_with(config)


def test_dg1022z_open_close_delegate_to_visa(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.open()
    dg_visa.open.assert_called_once()
    dg.close()
    dg_visa.close.assert_called_once()


# --- check_errors ---


def test_dg1022z_check_errors_returns_on_zero_code(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.check_errors()
    dg_visa.query.assert_called_once_with(":SYST:ERR?")


def test_dg1022z_check_errors_raises_on_nonzero_code(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.return_value = '-113,"Undefined header; keyword cannot be found"'
    with pytest.raises(RuntimeError, match="Rigol DG1022Z reported error -113: Undefined header"):
        dg.check_errors()


# --- set_waveform ---


def test_dg1022z_set_waveform_sine_writes_function_frequency_phase(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_waveform(1, Sine(frequency_hz=1000.0, phase_deg=90.0))
    assert dg_visa.write.call_args_list == [
        call(":SOUR1:FUNC SIN"),
        call(":SOUR1:FREQ 1000.0"),
        call(":SOUR1:PHAS 90.0"),
    ]


def test_dg1022z_set_waveform_maps_negative_phase_to_positive_degrees(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    """The instrument accepts 0-360 degrees; the definition normalizes to [-180, 180)."""
    dg.set_waveform(1, Sine(frequency_hz=1000.0, phase_deg=-90.0))
    assert call(":SOUR1:PHAS 270.0") in dg_visa.write.call_args_list


def test_dg1022z_set_waveform_uses_channel_2_suffix(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_waveform(2, Sine(frequency_hz=1000.0))
    assert dg_visa.write.call_args_list[0] == call(":SOUR2:FUNC SIN")


def test_dg1022z_set_waveform_square_writes_duty_cycle(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_waveform(1, Square(frequency_hz=1000.0, duty_cycle_pct=30.0))
    assert dg_visa.write.call_args_list == [
        call(":SOUR1:FUNC SQU"),
        call(":SOUR1:FREQ 1000.0"),
        call(":SOUR1:PHAS 0.0"),
        call(":SOUR1:FUNC:SQU:DCYC 30.0"),
    ]


def test_dg1022z_set_waveform_sawtooth_is_ramp_with_full_symmetry(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_waveform(1, Sawtooth(frequency_hz=1000.0))
    assert dg_visa.write.call_args_list == [
        call(":SOUR1:FUNC RAMP"),
        call(":SOUR1:FREQ 1000.0"),
        call(":SOUR1:PHAS 0.0"),
        call(":SOUR1:FUNC:RAMP:SYMM 100"),
    ]


def test_dg1022z_set_waveform_triangle_is_ramp_with_half_symmetry(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_waveform(1, Triangle(frequency_hz=1000.0))
    assert dg_visa.write.call_args_list == [
        call(":SOUR1:FUNC RAMP"),
        call(":SOUR1:FREQ 1000.0"),
        call(":SOUR1:PHAS 0.0"),
        call(":SOUR1:FUNC:RAMP:SYMM 50"),
    ]


def test_dg1022z_set_waveform_pulse_writes_width(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_waveform(1, Pulse(frequency_hz=1000.0, width_s=0.0005))
    assert dg_visa.write.call_args_list == [
        call(":SOUR1:FUNC PULS"),
        call(":SOUR1:FREQ 1000.0"),
        call(":SOUR1:FUNC:PULS:WIDT 0.0005"),
    ]


def test_dg1022z_set_waveform_pulse_rejects_delay(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    """The DG1000Z command set has no pulse-delay parameter."""
    with pytest.raises(ValueError, match="cannot program a pulse delay"):
        dg.set_waveform(1, Pulse(frequency_hz=1000.0, width_s=0.0001, delay_s=0.0001))
    dg_visa.write.assert_not_called()


def test_dg1022z_set_waveform_arbitrary_downloads_samples_and_sets_sample_rate(
    dg: RigolDG1022Z, dg_visa: MagicMock
) -> None:
    samples = (0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5)
    dg.set_waveform(1, Arbitrary(samples=samples, sample_rate_hz=1e6))
    assert dg_visa.write.call_args_list == [
        call(":SOUR1:TRAC:DATA VOLATILE,0.0,0.5,1.0,0.5,0.0,-0.5,-1.0,-0.5"),
        call(":SOUR1:FUNC:ARB:MODE SRAT"),
        call(":SOUR1:FUNC:ARB:SRAT 1000000.0"),
    ]


@pytest.mark.parametrize("num_points", [7, 16385])
def test_dg1022z_set_waveform_arbitrary_rejects_out_of_range_point_counts(
    dg: RigolDG1022Z, dg_visa: MagicMock, num_points: int
) -> None:
    samples = tuple(0.0 for _ in range(num_points))
    with pytest.raises(ValueError, match="accepts 8 to 16384 arbitrary points"):
        dg.set_waveform(1, Arbitrary(samples=samples, sample_rate_hz=1e6))
    dg_visa.write.assert_not_called()


def test_dg1022z_set_waveform_static_value_writes_dc_and_offset(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_waveform(1, StaticValue(value=1.5))
    assert dg_visa.write.call_args_list == [
        call(":SOUR1:FUNC DC"),
        call(":SOUR1:VOLT:OFFS 1.5"),
    ]


# --- get_waveform ---


def test_dg1022z_get_waveform_parses_sine(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.return_value = '"SIN,1.000000E+03,5.000000E+00,0.000000E+00,9.000000E+01"'
    assert dg.get_waveform(1) == Sine(frequency_hz=1000.0, phase_deg=90.0)
    dg_visa.query.assert_called_once_with(":SOUR1:APPL?")


def test_dg1022z_get_waveform_parses_square_with_duty_cycle(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.side_effect = [
        '"SQU,1.000000E+03,5.000000E+00,0.000000E+00,0.000000E+00"',
        "3.000000E+01",
    ]
    assert dg.get_waveform(1) == Square(frequency_hz=1000.0, duty_cycle_pct=30.0)
    assert dg_visa.query.call_args_list == [call(":SOUR1:APPL?"), call(":SOUR1:FUNC:SQU:DCYC?")]


def test_dg1022z_get_waveform_ramp_full_symmetry_is_sawtooth(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.side_effect = [
        '"RAMP,1.000000E+03,5.000000E+00,0.000000E+00,0.000000E+00"',
        "1.000000E+02",
    ]
    assert dg.get_waveform(1) == Sawtooth(frequency_hz=1000.0)


def test_dg1022z_get_waveform_ramp_partial_symmetry_is_triangle(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.side_effect = [
        '"RAMP,1.000000E+03,5.000000E+00,0.000000E+00,0.000000E+00"',
        "5.000000E+01",
    ]
    assert dg.get_waveform(1) == Triangle(frequency_hz=1000.0)


def test_dg1022z_get_waveform_parses_pulse_width(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.side_effect = [
        '"PULSE,1.000000E+03,5.000000E+00,0.000000E+00,0.000000E+00"',
        "5.000000E-04",
    ]
    assert dg.get_waveform(1) == Pulse(frequency_hz=1000.0, width_s=0.0005)
    assert dg_visa.query.call_args_list == [call(":SOUR1:APPL?"), call(":SOUR1:FUNC:PULS:WIDT?")]


def test_dg1022z_get_waveform_parses_dc_as_static_value(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.side_effect = ['"DC,DEF,DEF,2.000000E+00"', "2.000000E+00"]
    assert dg.get_waveform(1) == StaticValue(value=2.0)
    assert dg_visa.query.call_args_list == [call(":SOUR1:APPL?"), call(":SOUR1:VOLT:OFFS?")]


def test_dg1022z_get_waveform_user_returns_last_programmed_arbitrary(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    """Volatile arb sample data is not readable; the driver returns the last-programmed definition."""
    arb = Arbitrary(samples=(0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5), sample_rate_hz=1e6)
    dg.set_waveform(1, arb)
    dg_visa.query.return_value = '"USER,1.000000E+06,5.000000E+00,0.000000E+00,0.000000E+00"'
    assert dg.get_waveform(1) is arb


def test_dg1022z_get_waveform_user_without_programmed_arbitrary_raises(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.return_value = '"USER,1.000000E+06,5.000000E+00,0.000000E+00,0.000000E+00"'
    with pytest.raises(RuntimeError, match="not programmed by this driver"):
        dg.get_waveform(1)


def test_dg1022z_get_waveform_unsupported_function_raises(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.return_value = '"NOISE,DEF,5.000000E+00,0.000000E+00,DEF"'
    with pytest.raises(ValueError, match="unsupported waveform 'NOISE'"):
        dg.get_waveform(1)


# --- Amplitude ---


def test_dg1022z_set_amplitude_writes_unit_then_level(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VRMS)
    assert dg_visa.write.call_args_list == [
        call(":SOUR1:VOLT:UNIT VRMS"),
        call(":SOUR1:VOLT 2.5"),
    ]


def test_dg1022z_set_amplitude_rejects_vp_unit(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="no VP amplitude unit"):
        dg.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VP)
    dg_visa.write.assert_not_called()


def test_dg1022z_get_amplitude_parses_value_and_unit(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.side_effect = ["VPP", "5.000000E+00"]
    assert dg.get_amplitude(1) == (5.0, AmplitudeMeasurementUnit.VPP)
    assert dg_visa.query.call_args_list == [call(":SOUR1:VOLT:UNIT?"), call(":SOUR1:VOLT?")]


# --- Offset ---


def test_dg1022z_set_offset_writes(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_offset(1, 0.5)
    dg_visa.write.assert_called_once_with(":SOUR1:VOLT:OFFS 0.5")


def test_dg1022z_get_offset_parses_response(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.return_value = "5.000000E-01"
    assert dg.get_offset(1) == pytest.approx(0.5)
    dg_visa.query.assert_called_once_with(":SOUR1:VOLT:OFFS?")


# --- Output state ---


def test_dg1022z_output_enable_writes_on_off(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.output_enable(1, True)
    dg.output_enable(2, False)
    assert dg_visa.write.call_args_list == [call(":OUTP1 ON"), call(":OUTP2 OFF")]


def test_dg1022z_get_output_state_parses_on_off_words(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.return_value = "ON"
    assert dg.get_output_state(1) is True
    dg_visa.query.return_value = "OFF"
    assert dg.get_output_state(1) is False
    assert dg_visa.query.call_args_list == [call(":OUTP1?"), call(":OUTP1?")]


# --- Output load ---


def test_dg1022z_set_output_load_writes_ohms(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_output_load(1, 50.0)
    dg_visa.write.assert_called_once_with(":OUTP1:LOAD 50")


def test_dg1022z_set_output_load_high_z_writes_infinity(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.set_output_load(1, None)
    dg_visa.write.assert_called_once_with(":OUTP1:LOAD INF")


def test_dg1022z_get_output_load_parses_ohms(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.return_value = "5.000000E+01"
    assert dg.get_output_load(1) == pytest.approx(50.0)
    dg_visa.query.assert_called_once_with(":OUTP1:LOAD?")


def test_dg1022z_get_output_load_high_z_sentinel_returns_none(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg_visa.query.return_value = "9.900000E+37"
    assert dg.get_output_load(1) is None


# --- Phase alignment ---


def test_dg1022z_align_phase_writes_sync(dg: RigolDG1022Z, dg_visa: MagicMock) -> None:
    dg.align_phase()
    dg_visa.write.assert_called_once_with(":SOUR1:PHAS:SYNC")


# --- Channel validation ---


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_waveform", (Sine(frequency_hz=1000.0),)),
        ("get_waveform", ()),
        ("set_amplitude", (2.5, AmplitudeMeasurementUnit.VPP)),
        ("get_amplitude", ()),
        ("set_offset", (0.5,)),
        ("get_offset", ()),
        ("output_enable", (True,)),
        ("get_output_state", ()),
        ("set_output_load", (50.0,)),
        ("get_output_load", ()),
    ],
)
@pytest.mark.parametrize("channel", [0, 3])
def test_dg1022z_invalid_channel_raises_without_scpi(
    dg: RigolDG1022Z,
    dg_visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
    channel: int,
) -> None:
    with pytest.raises(ValueError, match="Rigol DG1022Z channel must be 1 or 2"):
        getattr(dg, method_name)(channel, *args)
    dg_visa.write.assert_not_called()
    dg_visa.query.assert_not_called()
