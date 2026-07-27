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
    Waveform,
)

_ARB_SAMPLES = (0.0, 0.5, 1.0, -1.0, 0.25, -0.25, 0.75, -0.75)


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

    with pytest.raises(RuntimeError, match="Rigol DG1022Z reported error -113: Undefined header"):
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
        call(":SOUR1:TRAC:DATA:POIN VOLATILE,8"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,1,8192"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,2,12287"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,3,16383"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,4,0"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,5,10239"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,6,6144"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,7,14335"),
        call(":SOUR1:TRAC:DATA:VAL VOLATILE,8,2048"),
    ]
    assert rigol_visa.query.call_count == 10


@pytest.mark.parametrize("num_points", [2, 16385], ids=["too_few", "too_many"])
def test_13_set_waveform_arbitrary_rejects_bad_point_counts(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
    num_points: int,
) -> None:
    waveform = Arbitrary(samples=(0.0,) * num_points, sample_rate_hz=1000000.0)

    with pytest.raises(ValueError, match="8 to 16384 arbitrary points"):
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

    rigol_visa.query.return_value = "5.000000E-01"
    assert rigol.get_offset(2) == pytest.approx(0.5)
    rigol_visa.query.assert_called_once_with(":SOUR2:VOLT:OFFS?")


def test_28_output_enable_formats_on_off_and_parses_state(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.output_enable(1, True)
    rigol.output_enable(2, False)
    assert rigol_visa.write.call_args_list == [call(":OUTP1 ON"), call(":OUTP2 OFF")]

    rigol_visa.query.return_value = "ON\n"
    assert rigol.get_output_state(1) is True
    rigol_visa.query.assert_called_once_with(":OUTP1?")

    rigol_visa.query.return_value = "OFF\n"
    assert rigol.get_output_state(1) is False


def test_29_output_load_roundtrip_and_high_z(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_output_load(1, 50.0)
    rigol.set_output_load(1, None)
    assert rigol_visa.write.call_args_list == [call(":OUTP1:LOAD 50"), call(":OUTP1:LOAD INF")]

    rigol_visa.query.return_value = "5.000000E+01"
    assert rigol.get_output_load(1) == pytest.approx(50.0)
    rigol_visa.query.assert_called_once_with(":OUTP1:LOAD?")

    rigol_visa.query.return_value = "9.900000E+37"
    assert rigol.get_output_load(1) is None


def test_30_align_phase_writes_sync(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.align_phase()

    rigol_visa.write.assert_called_once_with(":SOUR1:PHAS:SYNC")
