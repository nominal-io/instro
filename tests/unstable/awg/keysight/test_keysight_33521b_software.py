"""Software tests for the Keysight 33521B AWG driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import Keysight33521B
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
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

_NO_ERROR = '0,"No error"'


@pytest.fixture
def keysight_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.awg.drivers.keysight_33521b.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def keysight_visa(keysight_visa_cls: MagicMock) -> MagicMock:
    visa = keysight_visa_cls.return_value
    # Every write/read the driver issues is followed by its own :SYST:ERR? check now that
    # error-checking is private to the driver; default that check to clean so tests that don't
    # care about error handling don't have to stub it themselves.
    visa.query.return_value = _NO_ERROR
    return visa


@pytest.fixture
def keysight(keysight_visa_cls: MagicMock) -> Keysight33521B:
    return Keysight33521B("TCPIP0::keysight::INSTR")


def _query_sequence(keysight_visa: MagicMock, real_responses: list[str]) -> None:
    """Feed `real_responses` to the driver's real queries in order; :SYST:ERR? checks always read clean."""
    responses = iter(real_responses)

    def fake_query(command: str) -> str:
        if command == ":SYST:ERR?":
            return _NO_ERROR
        return next(responses)

    keysight_visa.query.side_effect = fake_query


def _real_query_calls(keysight_visa: MagicMock) -> list:
    """The driver's actual queries, excluding the interleaved :SYST:ERR? error checks."""
    return [c for c in keysight_visa.query.call_args_list if c != call(":SYST:ERR?")]


def test_01_init_builds_visa_driver_from_resource(keysight_visa_cls: MagicMock) -> None:
    Keysight33521B("TCPIP0::keysight::INSTR")

    keysight_visa_cls.assert_called_once_with("TCPIP0::keysight::INSTR")


def test_02_init_accepts_prebuilt_connection_config(keysight_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::keysight::INSTR")
    Keysight33521B(config)

    keysight_visa_cls.assert_called_once_with(config)


def test_03_open_close_delegate_to_visa(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight.open()
    keysight.close()

    keysight_visa.open.assert_called_once()
    keysight_visa.close.assert_called_once()


@pytest.mark.parametrize("response", ['0,"No error"', '+0,"No error"'])
def test_04_check_errors_accepts_zero_codes(keysight: Keysight33521B, keysight_visa: MagicMock, response: str) -> None:
    keysight_visa.query.return_value = response

    keysight._check_errors()

    keysight_visa.query.assert_called_once_with(":SYST:ERR?")


def test_05_check_errors_raises_on_nonzero_code(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '-113,"Undefined header"'

    with pytest.raises(RuntimeError, match=r'Keysight 33521B reported error: -113,"Undefined header"'):
        keysight._check_errors()


def test_06_set_waveform_sine_writes_function_frequency_phase(
    keysight: Keysight33521B,
    keysight_visa: MagicMock,
) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, Sine(frequency_hz=1000.0, phase_deg=90.0))

    assert keysight_visa.write.call_args_list == [
        call("FUNC SIN"),
        call("FREQ 1000.0"),
        call("PHAS 90.0"),
    ]


def test_07_set_waveform_wraps_negative_phase(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, Sine(frequency_hz=1000.0, phase_deg=-90.0))

    assert call("PHAS 270.0") in keysight_visa.write.call_args_list


def test_08_set_waveform_square_writes_duty_cycle(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
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
    keysight: Keysight33521B,
    keysight_visa: MagicMock,
    waveform: Waveform,
    expected_writes: list,
) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, waveform)

    assert keysight_visa.write.call_args_list == expected_writes


def test_10_set_waveform_pulse_writes_width(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, Pulse(frequency_hz=1000.0, width_s=0.0002))

    assert keysight_visa.write.call_args_list == [
        call("FUNC PULS"),
        call("FREQ 1000.0"),
        call("PHAS 0.0"),
        call("FUNC:PULS:WIDT 0.0002"),
    ]


def test_11_set_waveform_pulse_programs_delay_as_phase(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.set_waveform(1, Pulse(frequency_hz=1000.0, width_s=0.0002, delay_s=0.0001))

    assert keysight_visa.write.call_args_list == [
        call("FUNC PULS"),
        call("FREQ 1000.0"),
        call("PHAS 36.0"),
        call("FUNC:PULS:WIDT 0.0002"),
    ]


def test_12_set_waveform_arbitrary_downloads_normalized_samples(
    keysight: Keysight33521B,
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
    keysight: Keysight33521B,
    keysight_visa: MagicMock,
    num_points: int,
) -> None:
    waveform = Arbitrary(samples=(0.0,) * num_points, sample_rate_hz=1000000.0)

    with pytest.raises(ValueError, match="8 to 65536 arbitrary points"):
        keysight.set_waveform(1, waveform)

    keysight_visa.write.assert_not_called()


def test_14_set_waveform_static_value_writes_dc_offset(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight.set_waveform(1, StaticValue(value=1.5))

    assert keysight_visa.write.call_args_list == [
        call("FUNC DC"),
        call("VOLT:OFFS 1.5"),
    ]


def test_15_set_waveform_rejects_invalid_channel(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="only supports 1 channel"):
        keysight.set_waveform(2, Sine(frequency_hz=1000.0))

    keysight_visa.write.assert_not_called()


def test_16_get_waveform_parses_sine(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    _query_sequence(keysight_visa, ["SIN", "1.000000E+03", "9.000000E+01"])

    waveform = keysight.get_waveform(1)

    assert _real_query_calls(keysight_visa) == [call("FUNC?"), call("FREQ?"), call("PHAS?")]
    assert waveform == Sine(frequency_hz=1000.0, phase_deg=90.0)


def test_17_get_waveform_parses_square_duty_cycle(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    _query_sequence(keysight_visa, ["SQU", "5.000000E+02", "0.000000E+00", "2.500000E+01"])

    waveform = keysight.get_waveform(1)

    assert _real_query_calls(keysight_visa) == [
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
    keysight: Keysight33521B,
    keysight_visa: MagicMock,
    function_reply: str,
    expected: Waveform,
) -> None:
    _query_sequence(keysight_visa, [function_reply, "1.000000E+02", "0.000000E+00"])

    waveform = keysight.get_waveform(1)

    assert waveform == expected


def test_19_get_waveform_parses_pulse_width_and_delay(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    _query_sequence(keysight_visa, ["PULS", "1.000000E+03", "3.600000E+01", "2.000000E-04"])

    waveform = keysight.get_waveform(1)

    assert _real_query_calls(keysight_visa) == [
        call("FUNC?"),
        call("FREQ?"),
        call("PHAS?"),
        call("FUNC:PULS:WIDT?"),
    ]
    assert waveform == Pulse(frequency_hz=1000.0, width_s=0.0002, delay_s=0.0001)


def test_20_get_waveform_parses_static_value(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    _query_sequence(keysight_visa, ["DC", "1.500000E+00"])

    waveform = keysight.get_waveform(1)

    assert _real_query_calls(keysight_visa) == [call("FUNC?"), call("VOLT:OFFS?")]
    assert waveform == StaticValue(value=1.5)


def test_21_get_waveform_returns_cached_arbitrary(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    arbitrary = Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=1000000.0)
    keysight.set_waveform(1, arbitrary)
    _query_sequence(keysight_visa, ["ARB"])

    assert keysight.get_waveform(1) is arbitrary


def test_22_get_waveform_unprogrammed_arbitrary_raises(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    _query_sequence(keysight_visa, ["ARB"])

    with pytest.raises(RuntimeError, match="not programmed by this driver"):
        keysight.get_waveform(1)


def test_23_get_waveform_unknown_shape_raises(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = "NOIS"

    with pytest.raises(ValueError, match="unsupported waveform 'NOIS'"):
        keysight.get_waveform(1)


def test_24_set_amplitude_writes_unit_then_value(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VPP)

    assert keysight_visa.write.call_args_list == [
        call("VOLT:UNIT VPP"),
        call("VOLT 2.5"),
    ]


def test_25_set_amplitude_rejects_vp_unit(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="VP is not supported"):
        keysight.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VP)

    keysight_visa.write.assert_not_called()


def test_26_get_amplitude_parses_value_and_unit(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    _query_sequence(keysight_visa, ["1.000000E+00", "VRMS\n"])

    amplitude, unit = keysight.get_amplitude(1)

    assert _real_query_calls(keysight_visa) == [call("VOLT?"), call("VOLT:UNIT?")]
    assert amplitude == pytest.approx(1.0)
    assert unit is AmplitudeMeasurementUnit.VRMS


def test_27_offset_roundtrip_uses_offset_commands(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight.set_offset(1, 0.5)
    keysight_visa.write.assert_called_once_with("VOLT:OFFS 0.5")

    _query_sequence(keysight_visa, ["5.000000E-01"])
    assert keysight.get_offset(1) == pytest.approx(0.5)
    assert _real_query_calls(keysight_visa) == [call("VOLT:OFFS?")]


def test_28_output_enable_formats_on_off_and_parses_state(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight.output_enable(1, True)
    keysight.output_enable(1, False)
    assert keysight_visa.write.call_args_list == [call("OUTP ON"), call("OUTP OFF")]

    _query_sequence(keysight_visa, ["1"])
    assert keysight.get_output_state(1) is True
    assert _real_query_calls(keysight_visa) == [call("OUTP?")]

    _query_sequence(keysight_visa, ["0"])
    assert keysight.get_output_state(1) is False


def test_29_output_load_roundtrip_and_high_z(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight.set_output_load(1, 50.0)
    keysight.set_output_load(1, None)
    assert keysight_visa.write.call_args_list == [call("OUTP:LOAD 50.0"), call("OUTP:LOAD INF")]

    _query_sequence(keysight_visa, ["5.000000E+01"])
    assert keysight.get_output_load(1) == pytest.approx(50.0)
    assert _real_query_calls(keysight_visa) == [call("OUTP:LOAD?")]

    _query_sequence(keysight_visa, ["9.900000E+37"])
    assert keysight.get_output_load(1) is None


# ---------------------------------------------------------------------------
# Modulation
# ---------------------------------------------------------------------------

_SINE_CARRIER_RESPONSES = ["SIN"]
_PULSE_CARRIER_RESPONSES = ["PULS"]
_STATICVALUE_CARRIER_RESPONSES = ["DC"]
_ARB_CARRIER_RESPONSES = ["ARB"]


def _mock_carrier_query(
    keysight_visa: MagicMock,
    carrier_responses: list[str] | None = None,
    error_response: str = '0,"No error"',
) -> None:
    """Answer the pre-flight :STAT?, FUNC?, and :SYST:ERR? queries for set_modulation() tests; modulation defaults to disabled, carrier defaults to a Sine."""
    responses = list(carrier_responses if carrier_responses is not None else _SINE_CARRIER_RESPONSES)

    def fake_query(command: str) -> str:
        if command == ":SYST:ERR?":
            return error_response
        if command.endswith(":STAT?"):
            return "0"
        return responses.pop(0)

    keysight_visa.query.side_effect = fake_query


@pytest.mark.parametrize(
    ("mod_type", "shape", "magnitude", "carrier_responses", "expected_calls"),
    [
        (
            ModulationType.AM,
            Sine(frequency_hz=100.0),
            50.0,
            _SINE_CARRIER_RESPONSES,
            [
                call("AM:SOUR INT"),
                call("AM:INT:FUNC SIN"),
                call("AM:INT:FREQ 100.0"),
                call("AM:DEPT 50.0"),
            ],
        ),
        (
            ModulationType.FM,
            Square(frequency_hz=100.0),
            500.0,
            _SINE_CARRIER_RESPONSES,
            [
                call("FM:SOUR INT"),
                call("FM:INT:FUNC SQU"),
                call("FM:INT:FREQ 100.0"),
                call("FM:DEV 500.0"),
            ],
        ),
        (
            ModulationType.PM,
            Triangle(frequency_hz=100.0),
            45.0,
            _SINE_CARRIER_RESPONSES,
            [
                call("PM:SOUR INT"),
                call("PM:INT:FUNC TRI"),
                call("PM:INT:FREQ 100.0"),
                call("PM:DEV 45.0"),
            ],
        ),
        (
            ModulationType.PWM,
            Square(frequency_hz=100.0),
            50e-6,
            _PULSE_CARRIER_RESPONSES,
            [
                call("PWM:SOUR INT"),
                call("PWM:INT:FUNC SQU"),
                call("PWM:INT:FREQ 100.0"),
                call("PWM:DEV 5e-05"),
            ],
        ),
        (
            ModulationType.FSK,
            Sawtooth(frequency_hz=100.0),
            2000.0,
            _SINE_CARRIER_RESPONSES,
            [
                call("FSK:SOUR INT"),
                call("FSK:INT:RATE 100.0"),
                call("FSK:FREQ 2000.0"),
            ],
        ),
        (
            ModulationType.PSK,
            Triangle(frequency_hz=100.0),
            90.0,
            _SINE_CARRIER_RESPONSES,
            [
                call("BPSK:SOUR INT"),
                call("BPSK:INT:RATE 100.0"),
                call("BPSK:PHAS 90.0"),
            ],
        ),
    ],
    ids=["am", "fm", "pm", "pwm", "fsk", "psk"],
)
def test_30_set_modulation_writes_type_specific_commands(
    keysight: Keysight33521B,
    keysight_visa: MagicMock,
    mod_type: ModulationType,
    shape: Waveform,
    magnitude: float,
    carrier_responses: list[str],
    expected_calls: list,
) -> None:
    _mock_carrier_query(keysight_visa, carrier_responses)

    keysight.set_modulation(1, mod_type, shape, magnitude)

    assert keysight_visa.write.call_args_list == expected_calls


def test_31_set_modulation_rejects_ask(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="ASK modulation is not supported"):
        keysight.set_modulation(1, ModulationType.ASK, Sine(frequency_hz=100.0), 3.0)

    keysight_visa.write.assert_not_called()


@pytest.mark.parametrize(
    ("carrier_responses", "mod_type", "match"),
    [
        (_PULSE_CARRIER_RESPONSES, ModulationType.AM, "cannot apply AM modulation to a Pulse carrier"),
        (
            _SINE_CARRIER_RESPONSES,
            ModulationType.PWM,
            "can only apply PWM modulation to a Pulse carrier, not Sine",
        ),
        (_STATICVALUE_CARRIER_RESPONSES, ModulationType.AM, "cannot apply AM modulation to a StaticValue carrier"),
    ],
    ids=["pulse_carrier_non_pwm", "non_pulse_carrier_pwm", "staticvalue_carrier"],
)
def test_32_set_modulation_rejects_incompatible_carrier(
    keysight: Keysight33521B,
    keysight_visa: MagicMock,
    carrier_responses: list[str],
    mod_type: ModulationType,
    match: str,
) -> None:
    _mock_carrier_query(keysight_visa, carrier_responses)

    with pytest.raises(ValueError, match=match):
        keysight.set_modulation(1, mod_type, Sine(frequency_hz=100.0), 50.0)

    keysight_visa.write.assert_not_called()


def test_33_modulation_enable_true_enables_the_last_set_modulation_type(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    """Regression: PSK's SCPI prefix is BPSK, not ModulationType.PSK.value ("PSK")."""
    _mock_carrier_query(keysight_visa)
    keysight.set_modulation(1, ModulationType.PSK, Triangle(frequency_hz=100.0), 90.0)
    keysight_visa.write.reset_mock()

    keysight.modulation_enable(1, True)

    assert keysight_visa.write.call_args_list == [call("BPSK:STAT ON")]


def test_34_modulation_enable_true_raises_when_no_modulation_configured(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    keysight_visa.query.return_value = "0"

    with pytest.raises(RuntimeError, match="no modulation type currently configured"):
        keysight.modulation_enable(1, True)

    keysight_visa.write.assert_not_called()


def test_35_modulation_enable_writes_off_for_every_modulation_type(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    keysight_visa.query.return_value = '0,"No error"'

    keysight.modulation_enable(1, False)

    assert keysight_visa.write.call_args_list == [
        call("AM:STAT OFF"),
        call("FM:STAT OFF"),
        call("PM:STAT OFF"),
        call("PWM:STAT OFF"),
        call("FSK:STAT OFF"),
        call("BPSK:STAT OFF"),
    ]


def test_36_get_modulation_type_raises_when_none_enabled(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    with pytest.raises(RuntimeError, match="no modulation type currently configured"):
        keysight.get_modulation_type(1)

    keysight_visa.query.assert_not_called()


def test_37_get_modulation_type_returns_the_last_configured_type_without_querying_hardware(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    """get_modulation_type is a pure cache read: the 33521B has no unified modulation-type register to poll."""
    _mock_carrier_query(keysight_visa)
    keysight.set_modulation(1, ModulationType.PSK, Triangle(frequency_hz=100.0), 90.0)
    keysight_visa.query.reset_mock()

    assert keysight.get_modulation_type(1) == ModulationType.PSK
    keysight_visa.query.assert_not_called()


def test_38_get_modulation_state_false_when_none_enabled(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = "0"

    assert keysight.get_modulation_state(1) is False


def test_39_get_modulation_state_true_when_any_type_enabled(keysight: Keysight33521B, keysight_visa: MagicMock) -> None:
    _query_sequence(keysight_visa, ["1"])

    assert keysight.get_modulation_state(1) is True
    assert _real_query_calls(keysight_visa) == [call("AM:STAT?")]


def test_40_get_waveform_clamps_negative_phase_noise_to_zero_delay(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    _query_sequence(keysight_visa, ["PULS", "1.000000E+03", "-1.000000E-09", "2.000000E-04"])

    waveform = keysight.get_waveform(1)

    assert waveform == Pulse(frequency_hz=1000.0, width_s=0.0002, delay_s=0.0)


def test_41_set_modulation_accepts_arbitrary_carrier_not_programmed_by_this_driver(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    """Regression: an Arbitrary carrier this driver instance never downloaded is still a valid AM carrier."""
    _mock_carrier_query(keysight_visa, _ARB_CARRIER_RESPONSES)

    keysight.set_modulation(1, ModulationType.AM, Sine(frequency_hz=100.0), 50.0)

    assert call("AM:DEPT 50.0") in keysight_visa.write.call_args_list


def test_42_set_modulation_validates_carrier_with_a_single_query(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    """Carrier validation costs one FUNC? query, not a full get_waveform() round trip."""
    _mock_carrier_query(keysight_visa)

    keysight.set_modulation(1, ModulationType.AM, Sine(frequency_hz=100.0), 50.0)

    real_calls = _real_query_calls(keysight_visa)
    assert real_calls.count(call("FUNC?")) == 1


def test_43_get_waveform_unprogrammed_arbitrary_surfaces_pending_error_instead_of_masking_it(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    """Regression: a real pending SCPI error must surface, not be swallowed by the 'not programmed' message."""

    def fake_query(command: str) -> str:
        if command == ":SYST:ERR?":
            return '-113,"Undefined header"'
        return "ARB"

    keysight_visa.query.side_effect = fake_query

    with pytest.raises(RuntimeError, match="Undefined header"):
        keysight.get_waveform(1)


def test_44_set_modulation_skips_disable_when_previous_type_was_not_enabled(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    """Hardware-confirmed: reconfiguring modulation types needs no disable-all step when the previous type was never enabled."""
    _mock_carrier_query(keysight_visa)
    keysight.set_modulation(1, ModulationType.FSK, Sawtooth(frequency_hz=100.0), 2000.0)
    keysight_visa.write.reset_mock()
    _mock_carrier_query(keysight_visa)

    keysight.set_modulation(1, ModulationType.PSK, Triangle(frequency_hz=100.0), 90.0)

    assert keysight_visa.write.call_args_list == [
        call("BPSK:SOUR INT"),
        call("BPSK:INT:RATE 100.0"),
        call("BPSK:PHAS 90.0"),
    ]


def test_45_set_modulation_disables_and_reenables_when_previous_type_was_enabled(
    keysight: Keysight33521B, keysight_visa: MagicMock
) -> None:
    """Hardware-confirmed: switching modulation types while the previous type is enabled disables every type first, then re-enables only the new one."""
    _mock_carrier_query(keysight_visa)
    keysight.set_modulation(1, ModulationType.FSK, Sawtooth(frequency_hz=100.0), 2000.0)
    keysight.modulation_enable(1, True)
    keysight_visa.write.reset_mock()

    def fake_query(command: str) -> str:
        if command == ":SYST:ERR?":
            return _NO_ERROR
        if command.endswith(":STAT?"):
            return "1"
        return "SIN"

    keysight_visa.query.side_effect = fake_query

    keysight.set_modulation(1, ModulationType.PSK, Triangle(frequency_hz=100.0), 90.0)

    assert keysight_visa.write.call_args_list == [
        call("AM:STAT OFF"),
        call("FM:STAT OFF"),
        call("PM:STAT OFF"),
        call("PWM:STAT OFF"),
        call("FSK:STAT OFF"),
        call("BPSK:STAT OFF"),
        call("BPSK:SOUR INT"),
        call("BPSK:INT:RATE 100.0"),
        call("BPSK:PHAS 90.0"),
        call("BPSK:STAT ON"),
    ]
