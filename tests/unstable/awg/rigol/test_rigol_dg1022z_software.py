"""Software tests for the Rigol DG1022Z AWG driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    BurstTriggerSource,
    BurstType,
    GatePolarity,
    ModulationType,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    Triangle,
    Waveform,
)

_ARB_SAMPLES = (0.0, 0.5, 1.0, -1.0, 0.25, -0.25, 0.75, -0.75, 0.125)

_SINE_CARRIER_APPL_RESPONSE = '"SIN,1.000000E+03,5.000000E+00,0.000000E+00,0.000000E+00"'
_PULSE_CARRIER_APPL_RESPONSE = '"PULSE,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"'
_ARBITRARY_CARRIER_APPL_RESPONSE = '"USER,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"'
_DC_CARRIER_APPL_RESPONSE = '"DC,DEF,DEF,1.500000E+00"'

_NO_ERROR = '0,"No error"'


@pytest.fixture
def rigol_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.awg.drivers.rigol_dg1022z.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def rigol_visa(rigol_visa_cls: MagicMock) -> MagicMock:
    visa = rigol_visa_cls.return_value
    # Every write/read the driver issues is immediately followed by its own :SYST:ERR? check now
    # that error-checking is private to the driver; default that check to clean so tests that
    # don't care about error handling don't have to stub it themselves.
    visa.query.return_value = _NO_ERROR
    return visa


@pytest.fixture
def rigol(rigol_visa_cls: MagicMock) -> RigolDG1022Z:
    return RigolDG1022Z("TCPIP0::rigol::INSTR")


def _query_sequence(rigol_visa: MagicMock, real_responses: list[str]) -> None:
    """Feed `real_responses` to the driver's real queries in order; :SYST:ERR? checks always read clean."""
    responses = iter(real_responses)

    def fake_query(command: str) -> str:
        if command == ":SYST:ERR?":
            return _NO_ERROR
        return next(responses)

    rigol_visa.query.side_effect = fake_query


def _real_query_calls(rigol_visa: MagicMock) -> list:
    """The driver's actual queries, excluding the interleaved :SYST:ERR? error checks."""
    return [c for c in rigol_visa.query.call_args_list if c != call(":SYST:ERR?")]


def _mock_carrier_query(
    rigol_visa: MagicMock,
    carrier_response: str = _SINE_CARRIER_APPL_RESPONSE,
    error_response: str = "0",
    mod_type_response: str = "AM",
    mod_stat_response: str = "OFF",
    pulse_width_response: str = "2.000000E-04",
) -> None:
    """Answer get_waveform(), :SYST:ERR? checks, and :MOD:TYP?/:MOD:STAT? queries for set_modulation() tests."""

    def fake_query(command: str) -> str:
        if command == ":SYST:ERR?":
            return error_response
        if command.endswith(":MOD:TYP?"):
            return mod_type_response
        if command.endswith(":MOD:STAT?"):
            return mod_stat_response
        if command.endswith(":FUNC:PULS:WIDT?"):
            return pulse_width_response
        return carrier_response

    rigol_visa.query.side_effect = fake_query


# ---------------------------------------------------------------------------
# Construction, lifecycle, error checking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resource",
    ["TCPIP0::rigol::INSTR", VisaConfig(visa_resource="TCPIP0::rigol::INSTR")],
    ids=["string_resource", "visa_config"],
)
def test_01_init_builds_visa_driver_from_resource(rigol_visa_cls: MagicMock, resource: str | VisaConfig) -> None:
    RigolDG1022Z(resource)

    rigol_visa_cls.assert_called_once_with(resource)


def test_02_open_close_delegate_to_visa(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.open()
    rigol.close()

    rigol_visa.open.assert_called_once()
    rigol_visa.close.assert_called_once()


@pytest.mark.parametrize("response", ['0,"No error"', '+0,"No error"'])
def test_03_check_errors_accepts_zero_codes(rigol: RigolDG1022Z, rigol_visa: MagicMock, response: str) -> None:
    rigol_visa.query.return_value = response

    rigol._check_errors()

    rigol_visa.query.assert_called_once_with(":SYST:ERR?")


def test_04_check_errors_raises_on_nonzero_code(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol_visa.query.return_value = '-113,"Undefined header"'

    with pytest.raises(RuntimeError, match=r'Rigol DG1022Z reported error: -113,"Undefined header"'):
        rigol._check_errors()


# ---------------------------------------------------------------------------
# set_waveform / get_waveform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("waveform", "expected_calls"),
    [
        (
            Sine(frequency_hz=1000.0, phase_deg=90.0),
            [call(":SOUR1:FUNC SIN"), call(":SOUR1:FREQ 1000.0"), call(":SOUR1:PHAS 90.0")],
        ),
        (
            Sine(frequency_hz=1000.0, phase_deg=-90.0),
            [call(":SOUR1:FUNC SIN"), call(":SOUR1:FREQ 1000.0"), call(":SOUR1:PHAS 270.0")],
        ),
        (
            Square(frequency_hz=500.0, duty_cycle_pct=25.0),
            [
                call(":SOUR1:FUNC SQU"),
                call(":SOUR1:FREQ 500.0"),
                call(":SOUR1:PHAS 0.0"),
                call(":SOUR1:FUNC:SQU:DCYC 25.0"),
            ],
        ),
        (
            Sawtooth(frequency_hz=100.0),
            [
                call(":SOUR1:FUNC RAMP"),
                call(":SOUR1:FREQ 100.0"),
                call(":SOUR1:PHAS 0.0"),
                call(":SOUR1:FUNC:RAMP:SYMM 100"),
            ],
        ),
        (
            Triangle(frequency_hz=100.0),
            [
                call(":SOUR1:FUNC RAMP"),
                call(":SOUR1:FREQ 100.0"),
                call(":SOUR1:PHAS 0.0"),
                call(":SOUR1:FUNC:RAMP:SYMM 50"),
            ],
        ),
        (
            Pulse(frequency_hz=1000.0, width_s=0.0002),
            [call(":SOUR1:FUNC PULS"), call(":SOUR1:FREQ 1000.0"), call(":SOUR1:FUNC:PULS:WIDT 0.0002")],
        ),
        (
            StaticValue(value=1.5),
            [call(":SOUR1:FUNC DC"), call(":SOUR1:VOLT:OFFS 1.5")],
        ),
    ],
    ids=["sine", "sine_negative_phase", "square", "sawtooth", "triangle", "pulse", "staticvalue"],
)
def test_05_set_waveform_writes_shape_specific_commands(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, waveform: Waveform, expected_calls: list
) -> None:
    rigol.set_waveform(1, waveform)

    assert rigol_visa.write.call_args_list == expected_calls


@pytest.mark.parametrize(
    ("channel", "waveform", "match"),
    [
        (3, Sine(frequency_hz=1000.0), "channel must be 1 or 2"),
        (1, Pulse(frequency_hz=1000.0, width_s=0.0002, delay_s=0.0001), "cannot program a pulse delay"),
    ],
    ids=["invalid_channel", "pulse_nonzero_delay"],
)
def test_06_set_waveform_rejects_invalid_input(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, channel: int, waveform: Waveform, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        rigol.set_waveform(channel, waveform)

    rigol_visa.write.assert_not_called()


def test_07_set_waveform_arbitrary_writes_points_individually(
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
    assert rigol_visa.query.call_count == 11


@pytest.mark.parametrize("num_points", [2, 16385], ids=["too_few", "too_many"])
def test_08_set_waveform_arbitrary_rejects_bad_point_counts(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
    num_points: int,
) -> None:
    waveform = Arbitrary(samples=(0.0,) * num_points, sample_rate_hz=1000000.0)

    with pytest.raises(ValueError, match="9 to 16384 arbitrary points"):
        rigol.set_waveform(1, waveform)

    rigol_visa.write.assert_not_called()


@pytest.mark.parametrize(
    ("responses", "expected_queries", "expected"),
    [
        (
            ['"SIN,1.000000E+03,5.000000E+00,0.000000E+00,9.000000E+01"'],
            [call(":SOUR1:APPL?")],
            Sine(frequency_hz=1000.0, phase_deg=90.0),
        ),
        (
            ['"SQU,5.000000E+02,1.000000E+00,0.000000E+00,0.000000E+00"', "2.500000E+01"],
            [call(":SOUR1:APPL?"), call(":SOUR1:FUNC:SQU:DCYC?")],
            Square(frequency_hz=500.0, duty_cycle_pct=25.0, phase_deg=0.0),
        ),
        (
            ['"RAMP,1.000000E+02,1.000000E+00,0.000000E+00,0.000000E+00"', "1.000000E+02"],
            [call(":SOUR1:APPL?"), call(":SOUR1:FUNC:RAMP:SYMM?")],
            Sawtooth(frequency_hz=100.0),
        ),
        (
            ['"RAMP,1.000000E+02,1.000000E+00,0.000000E+00,0.000000E+00"', "5.000000E+01"],
            [call(":SOUR1:APPL?"), call(":SOUR1:FUNC:RAMP:SYMM?")],
            Triangle(frequency_hz=100.0),
        ),
        (
            ['"PULSE,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"', "2.000000E-04"],
            [call(":SOUR1:APPL?"), call(":SOUR1:FUNC:PULS:WIDT?")],
            Pulse(frequency_hz=1000.0, width_s=0.0002),
        ),
        (
            ['"DC,DEF,DEF,1.500000E+00"'],
            [call(":SOUR1:APPL?")],
            StaticValue(value=1.5),
        ),
    ],
    ids=["sine", "square", "sawtooth", "triangle", "pulse", "staticvalue"],
)
def test_09_get_waveform_parses_shape_specific_fields(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
    responses: list[str],
    expected_queries: list,
    expected: Waveform,
) -> None:
    _query_sequence(rigol_visa, responses)

    assert rigol.get_waveform(1) == expected
    assert _real_query_calls(rigol_visa) == expected_queries


def test_10_get_waveform_arbitrary_cache_and_unknown_shape_edge_cases(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    arbitrary = Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=1000000.0)
    rigol.set_waveform(1, arbitrary)

    # Channel 1 outputs USER and the driver has the samples cached from set_waveform above.
    _query_sequence(rigol_visa, ['"USER,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"'])
    assert rigol.get_waveform(1) is arbitrary

    # Channel 2 also outputs USER, but this driver never programmed it.
    _query_sequence(rigol_visa, ['"USER,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"'])
    with pytest.raises(RuntimeError, match="not programmed by this driver"):
        rigol.get_waveform(2)

    _query_sequence(rigol_visa, ['"NOIS,DEF,1.000000E+00,0.000000E+00"'])
    with pytest.raises(ValueError, match="unsupported waveform 'NOIS'"):
        rigol.get_waveform(1)


# ---------------------------------------------------------------------------
# Amplitude, offset, output, load, phase
# ---------------------------------------------------------------------------


def test_11_amplitude_roundtrip_writes_and_parses_unit_and_value(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VPP)
    assert rigol_visa.write.call_args_list == [call(":SOUR1:VOLT:UNIT VPP"), call(":SOUR1:VOLT 2.5")]

    _query_sequence(rigol_visa, ["VRMS\n", "1.000000E+00"])
    amplitude, unit = rigol.get_amplitude(2)
    assert _real_query_calls(rigol_visa) == [call(":SOUR2:VOLT:UNIT?"), call(":SOUR2:VOLT?")]
    assert amplitude == pytest.approx(1.0)
    assert unit is AmplitudeMeasurementUnit.VRMS


def test_12_set_amplitude_rejects_vp_unit(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="no VP amplitude unit"):
        rigol.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VP)

    rigol_visa.write.assert_not_called()


def test_13_offset_roundtrip_uses_offset_commands(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_offset(2, 0.5)
    rigol_visa.write.assert_called_once_with(":SOUR2:VOLT:OFFS 0.5")

    _query_sequence(
        rigol_visa,
        [
            '"SIN,1.000000E+03,1.000000E+00,0.000000E+00,0.000000E+00"',
            "5.000000E-01",
        ],
    )
    assert rigol.get_offset(2) == pytest.approx(0.5)
    assert _real_query_calls(rigol_visa) == [call(":SOUR2:APPL?"), call(":SOUR2:VOLT:OFFS?")]


def test_14_get_offset_dc_mode_uses_apply_reply(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    # In DC mode VOLT:OFFS? always reads 0 on the DG1000Z; only APPL? carries the level.
    _query_sequence(rigol_visa, ['"DC,DEF,DEF,7.500000E-01"'])

    assert rigol.get_offset(1) == pytest.approx(0.75)
    assert _real_query_calls(rigol_visa) == [call(":SOUR1:APPL?")]


def test_15_output_enable_formats_on_off_and_parses_state(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.output_enable(1, True)
    rigol.output_enable(2, False)
    assert rigol_visa.write.call_args_list == [call(":OUTP1 ON"), call(":OUTP2 OFF")]

    _query_sequence(rigol_visa, ["ON\n"])
    assert rigol.get_output_state(1) is True
    assert _real_query_calls(rigol_visa) == [call(":OUTP1?")]

    _query_sequence(rigol_visa, ["OFF\n"])
    assert rigol.get_output_state(1) is False


def test_16_output_load_roundtrip_and_high_z(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_output_load(1, 50.0)
    rigol.set_output_load(1, None)
    assert rigol_visa.write.call_args_list == [call(":OUTP1:LOAD 50"), call(":OUTP1:LOAD INF")]

    _query_sequence(rigol_visa, ["5.000000E+01"])
    assert rigol.get_output_load(1) == pytest.approx(50.0)
    assert _real_query_calls(rigol_visa) == [call(":OUTP1:LOAD?")]

    _query_sequence(rigol_visa, ["9.900000E+37"])
    assert rigol.get_output_load(1) is None


def test_17_align_phase_writes_sync(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.align_phase()

    rigol_visa.write.assert_called_once_with(":SOUR1:PHAS:SYNC")


# ---------------------------------------------------------------------------
# Modulation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mod_type", "shape", "magnitude", "carrier_response", "expected_calls"),
    [
        (
            ModulationType.AM,
            Sine(frequency_hz=100.0),
            50.0,
            _SINE_CARRIER_APPL_RESPONSE,
            [
                call(":SOUR1:AM:SOUR INT"),
                call(":SOUR1:AM:INT:FUNC SIN"),
                call(":SOUR1:AM:INT:FREQ 100.0"),
                call(":SOUR1:AM 50.0"),
                call(":SOUR1:MOD:TYP AM"),
            ],
        ),
        (
            ModulationType.FM,
            Square(frequency_hz=100.0),
            500.0,
            _SINE_CARRIER_APPL_RESPONSE,
            [
                call(":SOUR1:FM:SOUR INT"),
                call(":SOUR1:FM:INT:FUNC SQU"),
                call(":SOUR1:FM:INT:FREQ 100.0"),
                call(":SOUR1:FM 500.0"),
                call(":SOUR1:MOD:TYP FM"),
            ],
        ),
        (
            ModulationType.PM,
            Triangle(frequency_hz=100.0),
            45.0,
            _SINE_CARRIER_APPL_RESPONSE,
            [
                call(":SOUR1:PM:SOUR INT"),
                call(":SOUR1:PM:INT:FUNC TRI"),
                call(":SOUR1:PM:INT:FREQ 100.0"),
                call(":SOUR1:PM 45.0"),
                call(":SOUR1:MOD:TYP PM"),
            ],
        ),
        (
            ModulationType.PWM,
            Square(frequency_hz=100.0),
            50e-6,
            _PULSE_CARRIER_APPL_RESPONSE,
            [
                call(":SOUR1:PWM:SOUR INT"),
                call(":SOUR1:PWM:INT:FUNC SQU"),
                call(":SOUR1:PWM:INT:FREQ 100.0"),
                call(":SOUR1:PWM 5e-05"),
                call(":SOUR1:MOD:TYP PWM"),
            ],
        ),
        (
            ModulationType.ASK,
            Sine(frequency_hz=100.0),
            3.0,
            _SINE_CARRIER_APPL_RESPONSE,
            [
                call(":SOUR1:ASK:SOUR INT"),
                call(":SOUR1:ASK:INT 100.0"),
                call(":SOUR1:ASK:AMPL 3.0"),
                call(":SOUR1:MOD:TYP ASK"),
            ],
        ),
        (
            ModulationType.FSK,
            Sawtooth(frequency_hz=100.0),
            2000.0,
            _SINE_CARRIER_APPL_RESPONSE,
            [
                call(":SOUR1:FSK:SOUR INT"),
                call(":SOUR1:FSK:INT:RATE 100.0"),
                call(":SOUR1:FSK 2000.0"),
                call(":SOUR1:MOD:TYP FSK"),
            ],
        ),
        (
            ModulationType.PSK,
            Triangle(frequency_hz=100.0),
            90.0,
            _SINE_CARRIER_APPL_RESPONSE,
            [
                call(":SOUR1:PSK:SOUR INT"),
                call(":SOUR1:PSK:INT:RATE 100.0"),
                call(":SOUR1:PSK:PHAS 90.0"),
                call(":SOUR1:MOD:TYP PSK"),
            ],
        ),
        (
            ModulationType.AM,
            Sine(frequency_hz=100.0),
            50.0,
            _ARBITRARY_CARRIER_APPL_RESPONSE,
            [
                call(":SOUR1:AM:SOUR INT"),
                call(":SOUR1:AM:INT:FUNC SIN"),
                call(":SOUR1:AM:INT:FREQ 100.0"),
                call(":SOUR1:AM 50.0"),
                call(":SOUR1:MOD:TYP AM"),
            ],
        ),
    ],
    ids=["am", "fm", "pm", "pwm", "ask", "fsk", "psk", "am_with_arbitrary_carrier"],
)
def test_18_set_modulation_writes_type_specific_commands(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
    mod_type: ModulationType,
    shape: Waveform,
    magnitude: float,
    carrier_response: str,
    expected_calls: list,
) -> None:
    if carrier_response == _ARBITRARY_CARRIER_APPL_RESPONSE:
        rigol_visa.query.return_value = '0,"No error"'
        rigol.set_waveform(1, Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=1000000.0))
        rigol_visa.write.reset_mock()
    _mock_carrier_query(rigol_visa, carrier_response)

    rigol.set_modulation(1, mod_type, shape, magnitude)

    assert rigol_visa.write.call_args_list == expected_calls


@pytest.mark.parametrize(
    ("carrier_response", "mod_type", "match"),
    [
        (_PULSE_CARRIER_APPL_RESPONSE, ModulationType.AM, "cannot apply AM modulation to a Pulse carrier"),
        (
            _SINE_CARRIER_APPL_RESPONSE,
            ModulationType.PWM,
            "can only apply PWM modulation to a Pulse carrier, not Sine",
        ),
        ('"DC,DEF,DEF,1.500000E+00"', ModulationType.AM, "cannot apply AM modulation to a StaticValue carrier"),
    ],
    ids=["pulse_carrier_non_pwm", "non_pulse_carrier_pwm", "staticvalue_carrier"],
)
def test_19_set_modulation_rejects_incompatible_carrier(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, carrier_response: str, mod_type: ModulationType, match: str
) -> None:
    _mock_carrier_query(rigol_visa, carrier_response)

    with pytest.raises(ValueError, match=match):
        rigol.set_modulation(1, mod_type, Sine(frequency_hz=100.0), 50.0)

    rigol_visa.write.assert_not_called()


@pytest.mark.parametrize(
    ("channel", "enable", "expected_command"),
    [
        (1, True, ":SOUR1:MOD:STAT ON"),
        (1, False, ":SOUR1:MOD:STAT OFF"),
        (2, False, ":SOUR2:MOD:STAT OFF"),
    ],
    ids=["ch1_on", "ch1_off", "ch2_off"],
)
def test_20_modulation_enable_writes_mod_stat(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, channel: int, enable: bool, expected_command: str
) -> None:
    _mock_carrier_query(rigol_visa)

    rigol.modulation_enable(channel, enable)

    rigol_visa.write.assert_called_once_with(expected_command)


def test_21_modulation_enable_resends_command_on_repeat_calls(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """No cached/skip-guard state: repeat calls with the same value still hit the instrument every time."""
    _mock_carrier_query(rigol_visa)

    rigol.modulation_enable(1, True)
    rigol.modulation_enable(1, True)

    assert rigol_visa.write.call_args_list == [
        call(":SOUR1:MOD:STAT ON"),
        call(":SOUR1:MOD:STAT ON"),
    ]


def test_22_get_modulation_type_and_get_modulation_state_are_independent_per_channel(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    responses = {
        ":SOUR1:MOD:TYP?": "AM",
        ":SOUR1:MOD:STAT?": "OFF",
        ":SOUR2:MOD:TYP?": "FSK",
        ":SOUR2:MOD:STAT?": "ON",
    }
    rigol_visa.query.side_effect = lambda command: responses.get(command, _NO_ERROR)

    assert rigol.get_modulation_type(1) == ModulationType.AM
    assert rigol.get_modulation_state(1) is False
    assert rigol.get_modulation_type(2) == ModulationType.FSK
    assert rigol.get_modulation_state(2) is True


def test_23_get_modulation_type_raises_on_unexpected_instrument_response(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    """Regression guard: an instrument response outside the known ModulationType literals must fail loudly."""
    _mock_carrier_query(rigol_visa, mod_type_response="XYZ")

    with pytest.raises(ValueError, match="'XYZ' is not a valid ModulationType"):
        rigol.get_modulation_type(1)


# ---------------------------------------------------------------------------
# Burst
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("burst_type", "expected_mode"),
    [(BurstType.NCYCLE, "TRIG"), (BurstType.GATED, "GAT"), (BurstType.INFINITE, "INF")],
    ids=["ncycle", "gated", "infinite"],
)
def test_24_set_burst_writes_mode_for_each_burst_type(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, burst_type: BurstType, expected_mode: str
) -> None:
    _query_sequence(rigol_visa, [_SINE_CARRIER_APPL_RESPONSE])

    rigol.set_burst(1, burst_type)

    rigol_visa.write.assert_called_once_with(f":SOUR1:BURS:MODE {expected_mode}")


@pytest.mark.parametrize(
    ("channel", "burst_type", "carrier_response", "match"),
    [
        (3, BurstType.NCYCLE, _SINE_CARRIER_APPL_RESPONSE, "channel must be 1 or 2"),
        (1, "NCYCLE", _SINE_CARRIER_APPL_RESPONSE, "burst_type must be a BurstType"),
        (1, BurstType.NCYCLE, _DC_CARRIER_APPL_RESPONSE, "cannot burst a StaticValue"),
    ],
    ids=["invalid_channel", "invalid_burst_type", "staticvalue_carrier"],
)
def test_25_set_burst_rejects_invalid_input(
    rigol: RigolDG1022Z,
    rigol_visa: MagicMock,
    channel: int,
    burst_type: BurstType,
    carrier_response: str,
    match: str,
) -> None:
    _query_sequence(rigol_visa, [carrier_response])

    with pytest.raises((ValueError, TypeError), match=match):
        rigol.set_burst(channel, burst_type)

    rigol_visa.write.assert_not_called()


def test_26_burst_enable_writes_burs_stat_and_get_burst_state_parses_it(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    rigol.burst_enable(1, True)
    rigol.burst_enable(1, False)
    assert rigol_visa.write.call_args_list == [call(":SOUR1:BURS:STAT ON"), call(":SOUR1:BURS:STAT OFF")]

    _query_sequence(rigol_visa, ["ON\n"])
    assert rigol.get_burst_state(1) is True

    _query_sequence(rigol_visa, ["OFF\n"])
    assert rigol.get_burst_state(1) is False


def test_27_get_burst_type_parses_every_mode_and_rejects_unknown(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    _query_sequence(rigol_visa, ["TRIG"])
    assert rigol.get_burst_type(1) is BurstType.NCYCLE

    _query_sequence(rigol_visa, ["GAT"])
    assert rigol.get_burst_type(1) is BurstType.GATED

    _query_sequence(rigol_visa, ["INF"])
    assert rigol.get_burst_type(1) is BurstType.INFINITE

    _query_sequence(rigol_visa, ["NOIS"])
    with pytest.raises(ValueError, match="unsupported burst mode 'NOIS'"):
        rigol.get_burst_type(1)


@pytest.mark.parametrize(
    "source",
    [BurstTriggerSource.INTERNAL, BurstTriggerSource.EXTERNAL, BurstTriggerSource.MANUAL],
    ids=["internal", "external", "manual"],
)
def test_28_set_burst_trigger_writes_source(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, source: BurstTriggerSource
) -> None:
    _query_sequence(rigol_visa, ["TRIG"])

    rigol.set_burst_trigger(1, source)

    rigol_visa.write.assert_called_once_with(f":SOUR1:BURS:TRIG:SOUR {source.value}")


def test_29_set_burst_trigger_rejects_gated_mode_without_writing(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    """Regression guard: :BURS:TRIG:SOUR is rejected (-220) once :BURS:MODE GAT is set; don't even attempt it."""
    _query_sequence(rigol_visa, ["GAT"])

    with pytest.raises(ValueError, match="GATED burst mode"):
        rigol.set_burst_trigger(1, BurstTriggerSource.EXTERNAL)

    rigol_visa.write.assert_not_called()


def test_30_set_burst_trigger_rejects_internal_source_during_infinite_mode_without_writing(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    """Regression guard: INTERNAL trigger during INFINITE burst is rejected (-220) on the bench; don't attempt it."""
    _query_sequence(rigol_visa, ["INF"])

    with pytest.raises(ValueError, match="INFINITE burst mode"):
        rigol.set_burst_trigger(1, BurstTriggerSource.INTERNAL)

    rigol_visa.write.assert_not_called()


def test_31_set_burst_trigger_rejects_invalid_source_and_channel(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(TypeError, match="source must be a BurstTriggerSource"):
        rigol.set_burst_trigger(1, "INT")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.set_burst_trigger(3, BurstTriggerSource.INTERNAL)

    rigol_visa.write.assert_not_called()


def test_32_burst_delay_roundtrip_writes_and_parses(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_burst_delay(2, 0.5)
    rigol_visa.write.assert_called_once_with(":SOUR2:BURS:TDEL 0.5")

    _query_sequence(rigol_visa, ["5.000000E-01"])
    assert rigol.get_burst_delay(2) == pytest.approx(0.5)
    assert _real_query_calls(rigol_visa) == [call(":SOUR2:BURS:TDEL?")]


def test_33_set_burst_delay_rejects_negative_value_and_invalid_channel(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    with pytest.raises(ValueError, match="delay_s must be non-negative"):
        rigol.set_burst_delay(1, -0.1)

    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.set_burst_delay(3, 0.1)

    rigol_visa.write.assert_not_called()


@pytest.mark.parametrize("gate_polarity", [GatePolarity.NORM, GatePolarity.INV], ids=["norm", "inv"])
def test_34_gate_polarity_roundtrip_writes_and_parses(
    rigol: RigolDG1022Z, rigol_visa: MagicMock, gate_polarity: GatePolarity
) -> None:
    rigol.set_gate_polarity(1, gate_polarity)
    rigol_visa.write.assert_called_once_with(f":SOUR1:BURS:GATE:POL {gate_polarity.value}")

    _query_sequence(rigol_visa, [gate_polarity.value])
    assert rigol.get_gate_polarity(1) is gate_polarity


def test_35_set_gate_polarity_rejects_invalid_type_and_channel(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    with pytest.raises(TypeError, match="gate_polarity must be a GatePolarity"):
        rigol.set_gate_polarity(1, "NORM")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.set_gate_polarity(3, GatePolarity.NORM)

    rigol_visa.write.assert_not_called()


def test_36_ncycles_roundtrip_writes_and_parses(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_ncycles(1, 10)
    rigol_visa.write.assert_called_once_with(":SOUR1:BURS:NCYC 10")

    _query_sequence(rigol_visa, ["1.000000E+01"])
    assert rigol.get_burst_ncycles(1) == 10


def test_37_set_ncycles_rejects_non_positive_value_and_invalid_channel(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    with pytest.raises(ValueError, match="n_cycles must be >= 1"):
        rigol.set_ncycles(1, 0)

    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.set_ncycles(3, 10)

    rigol_visa.write.assert_not_called()


def test_38_burst_period_roundtrip_writes_and_parses(rigol: RigolDG1022Z, rigol_visa: MagicMock) -> None:
    rigol.set_burst_period(1, 0.25)
    rigol_visa.write.assert_called_once_with(":SOUR1:BURS:INT:PER 0.25")

    _query_sequence(rigol_visa, ["2.500000E-01"])
    assert rigol.get_burst_period(1) == pytest.approx(0.25)


def test_39_set_burst_period_rejects_non_positive_value_and_invalid_channel(
    rigol: RigolDG1022Z, rigol_visa: MagicMock
) -> None:
    with pytest.raises(ValueError, match="period must be positive"):
        rigol.set_burst_period(1, 0.0)

    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        rigol.set_burst_period(3, 0.1)

    rigol_visa.write.assert_not_called()
