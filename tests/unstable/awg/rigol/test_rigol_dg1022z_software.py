"""Software tests for the Rigol DG1022Z AWG driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    BurstType,
    HarmonicType,
    ModulationType,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    SweepType,
    Triangle,
    Waveform,
)

_ARB_SAMPLES = (0.0, 0.5, 1.0, -1.0, 0.25, -0.25, 0.75, -0.75, 0.125)


@pytest.fixture
def rigol_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.awg.drivers.rigol_dg1022z.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def rigol_visa(rigol_visa_cls: MagicMock) -> MagicMock:
    return rigol_visa_cls.return_value


@pytest.fixture
def rigol(rigol_visa_cls: MagicMock) -> RigolDG1022Z:
    return RigolDG1022Z("TCPIP0::rigol::INSTR")


def test_01_init_builds_visa_driver_from_resource(rigol_visa_cls: MagicMock) -> None:
    RigolDG1022Z("TCPIP0::rigol::INSTR")

    rigol_visa_cls.assert_called_once_with("TCPIP0::rigol::INSTR")


def test_02_init_accepts_prebuilt_connection_config(rigol_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::rigol::INSTR")
    RigolDG1022Z(config)

    rigol_visa_cls.assert_called_once_with(config)


def test_03_open_close_delegate_to_visa(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.open()
    rigol.close()

    rigol_visa.open.assert_called_once()
    rigol_visa.close.assert_called_once()


@pytest.mark.parametrize("response", ['0,"No error"', '+0,"No error"'])
def test_04_check_errors_accepts_zero_codes(rigol: RigolDG1022Z, rigol_visa: MagicMock, response: str) -> None:
    rigol_visa.query.return_value = response

    rigol.check_errors()

    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_05_check_errors_raises_on_nonzero_code(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = '-113,"Undefined header"'

    with pytest.raises(RuntimeError, match=r'Rigol DG1022Z reported error: -113,"Undefined header"'):
        rigol.check_errors()


def test_06_set_waveform_sine_writes_function_frequency_phase(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
) -> None:
    rigol.set_waveform(1, Sine(frequency_hz=1000.0, phase_deg=90.0))

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:FUNC SIN"),
        call(":SOUR1:FREQ 1000.0"),
        call(":SOUR1:PHAS 90.0"),
    ]


def test_07_set_waveform_wraps_negative_phase(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_waveform(1, Sine(frequency_hz=1000.0, phase_deg=-90.0))

    assert call(":SOUR1:PHAS 270.0") in rigol_visa.write.call_args_list


def test_08_set_waveform_square_writes_duty_cycle(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_waveform(2, Square(frequency_hz=500.0, duty_cycle_pct=25.0))

    assert rigol_visa.write.call_args_list == [
        call(":SOUR2:FUNC SQU"),
        call(":SOUR2:FREQ 500.0"),
        call(":SOUR2:PHAS 0.0"),
        call(":SOUR2:FUNC:SQU:DCYC 25.0"),
    ]


@pytest.mark.parametrize(
    ("waveform", "expected_symmetry"),
    [
        (Sawtooth(frequency_hz=100.0), 100),
        (Triangle(frequency_hz=100.0), 50),
    ],
    ids=["sawtooth", "triangle"],
)
def test_09_set_waveform_ramp_writes_symmetry(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
    waveform: Waveform,
    expected_symmetry: int,
) -> None:
    rigol.set_waveform(1, waveform)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:FUNC RAMP"),
        call(":SOUR1:FREQ 100.0"),
        call(":SOUR1:PHAS 0.0"),
        call(f":SOUR1:FUNC:RAMP:SYMM {expected_symmetry}"),
    ]


def test_10_set_waveform_pulse_writes_width(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_waveform(1, Pulse(frequency_hz=1000.0, width_s=0.0002))

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:FUNC PULS"),
        call(":SOUR1:FREQ 1000.0"),
        call(":SOUR1:FUNC:PULS:WIDT 0.0002"),
    ]


def test_11_set_waveform_pulse_rejects_nonzero_delay(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="cannot program a pulse delay"):
        rigol.set_waveform(1, Pulse(frequency_hz=1000.0, width_s=0.0002, delay_s=0.0001))

    rigol_visa.write.assert_not_called()


def test_12_set_waveform_arbitrary_writes_points_individually(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.return_value = '0,"No error"'

    rigol.set_waveform(1, Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=1000000.0))

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:APPL:ARB 1000000.0"),
        call(":SOUR1:TRAC:DATA:POIN VOLATILE,9"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,1,8192"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,2,12287"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,3,16383"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,4,0"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,5,10239"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,6,6144"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,7,14335"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,8,2048"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,9,9215"),
    ]
    # One error check after APPL:ARB, one after TRAC:DATA:POIN, and one per sample point
    # (9 samples) since check_errors only inspects the single oldest queued error rather
    # than draining the queue.
    assert rigol_visa.query.call_count == 11


@pytest.mark.parametrize("num_points", [2, 16385], ids=["too_few", "too_many"])
def test_13_set_waveform_arbitrary_rejects_bad_point_counts(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
    num_points: int,
) -> None:
    waveform = Arbitrary(samples=(0.0,) * num_points, sample_rate_hz=1000000.0)

    with pytest.raises(ValueError, match="9 to 16384 arbitrary points"):
        rigol.set_waveform(1, waveform)

    rigol_visa.write.assert_not_called()


def test_14_set_waveform_static_value_writes_dc_offset(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_waveform(1, StaticValue(value=1.5))

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:FUNC DC"),
        call(":SOUR1:VOLT:OFFS 1.5"),
    ]


def test_15_set_waveform_rejects_invalid_channel(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.set_waveform(3, Sine(frequency_hz=1000.0))

    rigol_visa.write.assert_not_called()


def test_16_get_waveform_parses_sine(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = '"SIN,1.000000E+03,5.000000E+00,0.000000E+00,9.000000E+01"'

    waveform = rigol.get_waveform(1)

    rigol_visa.query.assert_called_once_with(":SOUR1:APPL?")
    assert waveform == Sine(frequency_hz=1000.0, phase_deg=90.0)


def test_17_get_waveform_parses_square_duty_cycle(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = [
        '"SQU,5.000000E+02,1.000000E+00,0.000000E+00,0.000000E+00"',
        "2.500000E+01",
    ]

    waveform = rigol.get_waveform(2)

    assert rigol_visa.query.call_args_list == [call(":SOUR2:APPL?"), call(":SOUR2:FUNC:SQU:DCYC?")]
    assert waveform == Square(frequency_hz=500.0, duty_cycle_pct=25.0, phase_deg=0.0)


@pytest.mark.parametrize(
    ("symmetry_response", "expected"),
    [
        ("1.000000E+02", Sawtooth(frequency_hz=100.0)),
        ("5.000000E+01", Triangle(frequency_hz=100.0)),
    ],
    ids=["sawtooth", "triangle"],
)
def test_18_get_waveform_distinguishes_sawtooth_and_triangle(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
    symmetry_response: str,
    expected: Waveform,
) -> None:
    rigol_visa.query.side_effect = [
        '"RAMP,1.000000E+02,1.000000E+00,0.000000E+00,0.000000E+00"',
        symmetry_response,
    ]

    waveform = rigol.get_waveform(1)

    assert rigol_visa.query.call_args_list == [call(":SOUR1:APPL?"), call(":SOUR1:FUNC:RAMP:SYMM?")]
    assert waveform == expected


def test_19_get_waveform_parses_pulse_width(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = [
        '"PULSE,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"',
        "2.000000E-04",
    ]

    waveform = rigol.get_waveform(1)

    assert rigol_visa.query.call_args_list == [call(":SOUR1:APPL?"), call(":SOUR1:FUNC:PULS:WIDT?")]
    assert waveform == Pulse(frequency_hz=1000.0, width_s=0.0002)


def test_20_get_waveform_parses_static_value_from_apply_reply(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
) -> None:
    rigol_visa.query.return_value = '"DC,DEF,DEF,1.500000E+00"'

    waveform = rigol.get_waveform(1)

    # In DC mode VOLT:OFFS? always reads 0 on the DG1000Z; only APPL? carries the level.
    rigol_visa.query.assert_called_once_with(":SOUR1:APPL?")
    assert waveform == StaticValue(value=1.5)


def test_21_get_waveform_returns_cached_arbitrary(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    arbitrary = Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=1000000.0)
    rigol_visa.query.return_value = '0,"No error"'
    rigol.set_waveform(1, arbitrary)
    rigol_visa.query.return_value = '"USER,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"'

    assert rigol.get_waveform(1) is arbitrary


def test_22_get_waveform_unprogrammed_arbitrary_raises(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = '"USER,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"'

    with pytest.raises(RuntimeError, match="not programmed by this driver"):
        rigol.get_waveform(1)


def test_23_get_waveform_unknown_shape_raises(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = '"NOIS,DEF,1.000000E+00,0.000000E+00"'

    with pytest.raises(ValueError, match="unsupported waveform 'NOIS'"):
        rigol.get_waveform(1)


def test_24_set_amplitude_writes_unit_then_value(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VPP)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:VOLT:UNIT VPP"),
        call(":SOUR1:VOLT 2.5"),
    ]


def test_25_set_amplitude_rejects_vp_unit(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="no VP amplitude unit"):
        rigol.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VP)

    rigol_visa.write.assert_not_called()


def test_26_get_amplitude_parses_value_and_unit(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = ["VRMS\n", "1.000000E+00"]

    amplitude, unit = rigol.get_amplitude(2)

    assert rigol_visa.query.call_args_list == [call(":SOUR2:VOLT:UNIT?"), call(":SOUR2:VOLT?")]
    assert amplitude == pytest.approx(1.0)
    assert unit is AmplitudeMeasurementUnit.VRMS


def test_27_offset_roundtrip_uses_offset_commands(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_offset(2, 0.5)
    rigol_visa.write.assert_called_once_with(":SOUR2:VOLT:OFFS 0.5")

    rigol_visa.query.side_effect = [
        '"SIN,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"',
        "5.000000E-01",
    ]
    assert rigol.get_offset(2) == pytest.approx(0.5)
    assert rigol_visa.query.call_args_list == [call(":SOUR2:APPL?"), call(":SOUR2:VOLT:OFFS?")]


def test_28_get_offset_dc_mode_uses_apply_reply(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    # In DC mode VOLT:OFFS? always reads 0 on the DG1000Z; only APPL? carries the level.
    rigol_visa.query.return_value = '"DC,DEF,DEF,7.500000E-01"'

    assert rigol.get_offset(1) == pytest.approx(0.75)
    rigol_visa.query.assert_called_once_with(":SOUR1:APPL?")


def test_29_output_enable_formats_on_off_and_parses_state(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.output_enable(1, True)
    rigol.output_enable(2, False)
    assert rigol_visa.write.call_args_list == [call(":OUTP1 ON"), call(":OUTP2 OFF")]

    rigol_visa.query.return_value = "ON\n"
    assert rigol.get_output_state(1) is True
    rigol_visa.query.assert_called_once_with(":OUTP1?")

    rigol_visa.query.return_value = "OFF\n"
    assert rigol.get_output_state(1) is False


def test_30_output_load_roundtrip_and_high_z(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_output_load(1, 50.0)
    rigol.set_output_load(1, None)
    assert rigol_visa.write.call_args_list == [call(":OUTP1:LOAD 50"), call(":OUTP1:LOAD INF")]

    rigol_visa.query.return_value = "5.000000E+01"
    assert rigol.get_output_load(1) == pytest.approx(50.0)
    rigol_visa.query.assert_called_once_with(":OUTP1:LOAD?")

    rigol_visa.query.return_value = "9.900000E+37"
    assert rigol.get_output_load(1) is None


def test_31_align_phase_writes_sync(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.align_phase()

    rigol_visa.write.assert_called_once_with(":SOUR1:PHAS:SYNC")


_SINE_CARRIER_RESPONSE = '"SIN,1.000000E+03,5.000000E+00,0.000000E+00,0.000000E+00"'


@pytest.mark.parametrize(
    ("mod_type", "shape", "prefix"),
    [
        (ModulationType.AM, Sine(frequency_hz=200.0), "AM"),
        (ModulationType.FM, Square(frequency_hz=300.0), "FM"),
        (ModulationType.PM, Sawtooth(frequency_hz=400.0), "PM"),
    ],
    ids=["am", "fm", "pm"],
)
def test_31_modulate_am_fm_pm_writes_internal_source_shape_and_state(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
    mod_type: ModulationType,
    shape: Waveform,
    prefix: str,
) -> None:
    rigol_visa.query.return_value = "OFF"
    rigol.modulate(1, mod_type, shape, 50.0)

    rigol_visa.query.assert_called_once_with(":SOUR1:HARM:STAT?")
    expected_function = {Sine: "SIN", Square: "SQU", Sawtooth: "RAMP", Triangle: "TRI"}[type(shape)]
    assert rigol_visa.write.call_args_list == [
        call(f":SOUR1:{prefix}:SOUR INT"),
        call(f":SOUR1:{prefix}:INT:FUNC {expected_function}"),
        call(f":SOUR1:{prefix}:INT:FREQ {shape.frequency_hz}"),
        call(f":SOUR1:{prefix} 50.0"),
        call(f":SOUR1:{prefix}:STAT ON"),
    ]


def test_32_modulate_triangle_shape_maps_to_tri_function(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = "OFF"
    rigol.modulate(2, ModulationType.AM, Triangle(frequency_hz=1000.0), 10.0)

    assert call(":SOUR2:AM:INT:FUNC TRI") in rigol_visa.write.call_args_list


def test_33_modulate_ask_writes_internal_rate_and_amplitude(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = "OFF"
    rigol.modulate(1, ModulationType.ASK, Sine(frequency_hz=150.0), 2.5)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:ASK:SOUR INT"),
        call(":SOUR1:ASK:INT 150.0"),
        call(":SOUR1:ASK:AMPL 2.5"),
        call(":SOUR1:ASK:STAT ON"),
    ]


def test_34_modulate_fsk_writes_internal_rate_and_hop_frequency(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = "OFF"
    rigol.modulate(2, ModulationType.FSK, Square(frequency_hz=150.0), 5000.0)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR2:FSK:SOUR INT"),
        call(":SOUR2:FSK:INT:RATE 150.0"),
        call(":SOUR2:FSK 5000.0"),
        call(":SOUR2:FSK:STAT ON"),
    ]


@pytest.mark.parametrize("mod_type", [ModulationType.AM, ModulationType.ASK, ModulationType.FSK])
def test_35_modulate_rejects_unsupported_shape(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, mod_type: ModulationType
) -> None:
    """Shape validation is local and must run before any instrument I/O, including the carrier check."""
    with pytest.raises(ValueError, match="cannot use Pulse as a modulating waveform"):
        rigol.modulate(1, mod_type, Pulse(frequency_hz=1000.0, width_s=0.0002), 10.0)

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_36_modulate_rejects_invalid_channel(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.modulate(3, ModulationType.AM, Sine(frequency_hz=1000.0), 50.0)

    rigol_visa.write.assert_not_called()


@pytest.mark.parametrize(
    "harm_type", [ht for ht in HarmonicType if ht is not HarmonicType.USER], ids=lambda ht: ht.value.lower()
)
def test_39_enable_harmonics_writes_order_type_then_enables(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, harm_type: HarmonicType
) -> None:
    rigol_visa.query.return_value = _SINE_CARRIER_RESPONSE
    rigol.enable_harmonics(1, 4, harm_type)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:HARM:ORDE 4"),
        call(f":SOUR1:HARM:TYP {harm_type.value}"),
        call(":SOUR1:HARM:STAT ON"),
    ]


@pytest.mark.parametrize("order", [1, 9], ids=["too_low", "too_high"])
def test_40_enable_harmonics_rejects_order_out_of_range(rigol: RigolDG1022Z, rigol_visa: MagicMock, order: int) -> None:
    """Order validation is local and must run before any instrument I/O, including the carrier check."""
    with pytest.raises(ValueError, match="order is out of range"):
        rigol.enable_harmonics(1, order, HarmonicType.EVEN)

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_41_enable_harmonics_rejects_invalid_channel(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.enable_harmonics(3, 4, HarmonicType.EVEN)

    rigol_visa.write.assert_not_called()


def test_42_enable_harmonics_rejects_square_carrier(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """Square carrier requires a second duty-cycle query; use side_effect to feed both replies."""
    rigol_visa.query.side_effect = [
        '"SQU,5.000000E+02,1.000000E+00,0.000000E+00,0.000000E+00"',
        "5.000000E+01",
    ]

    with pytest.raises(ValueError, match="can only enable harmonics on a Sine wave; channel 1 outputs Square"):
        rigol.enable_harmonics(1, 4, HarmonicType.EVEN)

    rigol_visa.write.assert_not_called()


def test_43_get_waveform_treats_harmonic_appl_reply_as_sine(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """Once harmonics are enabled, APPL? reports 'HARMONIC' instead of 'SIN'; the carrier is still Sine."""
    rigol_visa.query.return_value = '"HARMONIC,1.000000E+03,5.000000E+00,0.000000E+00,4.500000E+01"'

    waveform = rigol.get_waveform(1)

    rigol_visa.query.assert_called_once_with(":SOUR1:APPL?")
    assert waveform == Sine(frequency_hz=1000.0, phase_deg=45.0)


def test_44_enable_harmonics_reconfigures_while_already_active(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """A channel already reporting 'HARMONIC' as its carrier must still pass the Sine-carrier check."""
    rigol_visa.query.return_value = '"HARMONIC,1.000000E+03,5.000000E+00,0.000000E+00,0.000000E+00"'

    rigol.enable_harmonics(1, 6, HarmonicType.ODD)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:HARM:ORDE 6"),
        call(":SOUR1:HARM:TYP ODD"),
        call(":SOUR1:HARM:STAT ON"),
    ]


def test_45_modulate_rejects_non_enum_mod_type(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """A bad mod_type must not silently fall through to the FSK branch; reject before any instrument I/O."""
    with pytest.raises(TypeError, match="mod_type must be a ModulationType, got str"):
        rigol.modulate(1, "AM", Sine(frequency_hz=1000.0), 50.0)  # type: ignore[arg-type]

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_46_enable_harmonics_rejects_non_enum_harm_type(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """A bad harm_type must be rejected before any instrument I/O, not after a partial write."""
    with pytest.raises(TypeError, match="harm_type must be a HarmonicType, got str"):
        rigol.enable_harmonics(1, 4, "EVEN")  # type: ignore[arg-type]

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_47_enable_harmonics_user_type_writes_bitmask(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """Callers supply only the 7 real bits for harmonics 2-8; the driver adds the fixed 'X' placeholder."""
    rigol_visa.query.return_value = _SINE_CARRIER_RESPONSE
    rigol.enable_harmonics(1, 4, HarmonicType.USER, user_harmonics="1010100")

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:HARM:ORDE 4"),
        call(":SOUR1:HARM:TYP USER"),
        call(":SOUR1:HARM:USER X1010100"),
        call(":SOUR1:HARM:STAT ON"),
    ]


@pytest.mark.parametrize(
    "user_harmonics",
    [None, "101010", "10101001", "10101XX", "1010 00"],
    ids=["missing", "too_short", "too_long", "non_binary", "whitespace"],
)
def test_48_enable_harmonics_rejects_invalid_user_harmonics(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, user_harmonics: str | None
) -> None:
    with pytest.raises(ValueError, match="user_harmonics must be a 7-character string"):
        rigol.enable_harmonics(1, 4, HarmonicType.USER, user_harmonics=user_harmonics)

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_49_enable_harmonics_rejects_user_harmonics_for_non_user_type(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    with pytest.raises(ValueError, match="user_harmonics is only valid when harm_type is HarmonicType.USER"):
        rigol.enable_harmonics(1, 4, HarmonicType.EVEN, user_harmonics="1010100")

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_50_modulate_rejects_when_harmonics_enabled(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = "ON"

    with pytest.raises(ValueError, match="cannot modulate channel 1 while harmonics are enabled"):
        rigol.modulate(1, ModulationType.AM, Sine(frequency_hz=200.0), 50.0)

    rigol_visa.query.assert_called_once_with(":SOUR1:HARM:STAT?")
    rigol_visa.write.assert_not_called()


def test_51_burst_ncycle_writes_mode_and_internal_trigger(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = ["OFF", _SINE_CARRIER_RESPONSE]
    rigol.burst(1, BurstType.NCYCLE)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:BURS:MODE TRIG"),
        call(":SOUR1:BURS:TRIG:SOUR INT"),
        call(":SOUR1:BURS:STAT ON"),
    ]


def test_52_burst_gated_skips_explicit_trigger_source(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """:BURS:MODE GAT locks the trigger source to EXTernal; an explicit write afterward is rejected."""
    rigol_visa.query.side_effect = ["OFF", _SINE_CARRIER_RESPONSE]
    rigol.burst(2, BurstType.GATED)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR2:BURS:MODE GAT"),
        call(":SOUR2:BURS:GATE:POL NORM"),
        call(":SOUR2:BURS:STAT ON"),
    ]


def test_53_burst_infinite_writes_manual_trigger_source(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """Infinite burst has no internal-trigger option; MANual is used instead."""
    rigol_visa.query.side_effect = ["OFF", _SINE_CARRIER_RESPONSE]
    rigol.burst(1, BurstType.INFINITE)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:BURS:MODE INF"),
        call(":SOUR1:BURS:TRIG:SOUR MAN"),
        call(":SOUR1:BURS:STAT ON"),
    ]


def test_54_burst_rejects_invalid_channel(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.burst(3, BurstType.NCYCLE)

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_55_burst_rejects_non_enum_burst_type(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(TypeError, match="burst_type must be a BurstType, got str"):
        rigol.burst(1, "NCYCLE")  # type: ignore[arg-type]

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_56_burst_rejects_when_harmonics_enabled(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = "ON"

    with pytest.raises(ValueError, match="cannot burst channel 1 while harmonics are enabled"):
        rigol.burst(1, BurstType.NCYCLE)

    rigol_visa.query.assert_called_once_with(":SOUR1:HARM:STAT?")
    rigol_visa.write.assert_not_called()


def test_57_burst_rejects_static_value_carrier(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """The instrument silently leaves burst disabled for DC with no error; reject it client-side."""
    rigol_visa.query.side_effect = ["OFF", '"DC,DEF,DEF,5.000000E-01"']

    with pytest.raises(ValueError, match="cannot burst a StaticValue \\(DC\\) waveform on channel 1"):
        rigol.burst(1, BurstType.NCYCLE)

    rigol_visa.write.assert_not_called()


@pytest.mark.parametrize(
    ("sweep_type", "expected"),
    [
        (SweepType.LINEAR, "LIN"),
        (SweepType.LOG, "LOG"),
        (SweepType.STEP, "STEP"),
    ],
    ids=["linear", "log", "step"],
)
def test_58_sweep_writes_frequency_range_and_state(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, sweep_type: SweepType, expected: str
) -> None:
    rigol_visa.query.side_effect = ["OFF", _SINE_CARRIER_RESPONSE]
    rigol.sweep(1, 100.0, 200.0, sweep_type)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:FREQ:STAR 100.0"),
        call(":SOUR1:FREQ:STOP 200.0"),
        call(f":SOUR1:SWE:SPAC {expected}"),
        call(":SOUR1:SWE:TRIG:SOUR INT"),
        call(":SOUR1:SWE:STAT ON"),
    ]


def test_59_sweep_rejects_invalid_channel(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.sweep(3, 100.0, 200.0, SweepType.LINEAR)

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_60_sweep_rejects_non_enum_sweep_type(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(TypeError, match="sweep_type must be a SweepType, got str"):
        rigol.sweep(1, 100.0, 200.0, "LIN")  # type: ignore[arg-type]

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


@pytest.mark.parametrize(
    ("start_freq", "stop_freq"),
    [(0.0, 200.0), (100.0, 0.0), (-100.0, 200.0), (100.0, -200.0)],
    ids=["zero_start", "zero_stop", "negative_start", "negative_stop"],
)
def test_61_sweep_rejects_non_positive_frequency(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, start_freq: float, stop_freq: float
) -> None:
    with pytest.raises(ValueError, match="start_freq and stop_freq must be positive"):
        rigol.sweep(1, start_freq, stop_freq, SweepType.LINEAR)

    rigol_visa.query.assert_not_called()
    rigol_visa.write.assert_not_called()


def test_62_sweep_rejects_when_harmonics_enabled(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = "ON"

    with pytest.raises(ValueError, match="cannot sweep channel 1 while harmonics are enabled"):
        rigol.sweep(1, 100.0, 200.0, SweepType.LINEAR)

    rigol_visa.query.assert_called_once_with(":SOUR1:HARM:STAT?")
    rigol_visa.write.assert_not_called()


def test_63_sweep_rejects_pulse_carrier(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = [
        "OFF",
        '"PULSE,1.000000E+03,5.000000E+00,0.000000E+00,0.000000E+00"',
        "2.000000E-04",
    ]

    with pytest.raises(ValueError, match="cannot sweep a Pulse waveform on channel 1"):
        rigol.sweep(1, 100.0, 200.0, SweepType.LINEAR)

    rigol_visa.write.assert_not_called()


def test_64_sweep_rejects_static_value_carrier(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.side_effect = ["OFF", '"DC,DEF,DEF,5.000000E-01"']

    with pytest.raises(ValueError, match="cannot sweep a StaticValue waveform on channel 1"):
        rigol.sweep(1, 100.0, 200.0, SweepType.LINEAR)

    rigol_visa.write.assert_not_called()


def test_65_set_waveform_rejects_sweep_compatible_waveform_while_sweeping(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    """A sweep-compatible waveform left set while sweeping would silently target a dormant register."""
    rigol_visa.query.return_value = "ON"

    with pytest.raises(ValueError, match="cannot set a new waveform on channel 1 while a sweep is active"):
        rigol.set_waveform(1, Sine(frequency_hz=500.0))

    rigol_visa.query.assert_called_once_with(":SOUR1:SWE:STAT?")
    rigol_visa.write.assert_not_called()


def test_66_set_waveform_allows_pulse_while_sweeping(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """The instrument auto-disables an active sweep when switching to a non-sweepable waveform."""
    rigol_visa.query.return_value = "ON"

    rigol.set_waveform(1, Pulse(frequency_hz=1000.0, width_s=0.0002))

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:FUNC PULS"),
        call(":SOUR1:FREQ 1000.0"),
        call(":SOUR1:FUNC:PULS:WIDT 0.0002"),
    ]


def test_67_set_waveform_allows_static_value_while_sweeping(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = "ON"

    rigol.set_waveform(1, StaticValue(value=1.5))

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:FUNC DC"),
        call(":SOUR1:VOLT:OFFS 1.5"),
    ]
