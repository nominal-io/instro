"""Tests for AWGDriverBase contract, waveform definitions, and InstroAWG composition."""

import math
from dataclasses import FrozenInstanceError, replace
from typing import get_args
from unittest.mock import MagicMock

import pytest

from instro.unstable.awg.awg import _PUBLISHED_NAMES, AWGDriverBase, InstroAWG
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
    convert_amplitude,
)

# ---------------------------------------------------------------------------
# Minimal concrete driver — implements every abstract method with no-ops
# ---------------------------------------------------------------------------


class _MinimalAWGDriver(AWGDriverBase):
    """Satisfies the full abstract contract so we can test optional-method defaults."""

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def check_errors(self) -> None:
        pass

    def set_waveform(self, channel: int, waveform: Waveform) -> None:
        pass

    def get_waveform(self, channel: int) -> Waveform:
        return Sine(frequency_hz=1000.0)

    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
        pass

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        return (1.0, AmplitudeMeasurementUnit.VPP)

    def set_offset(self, channel: int, offset: float) -> None:
        pass

    def get_offset(self, channel: int) -> float:
        return 0.0

    def output_enable(self, channel: int, enable: bool) -> None:
        pass

    def get_output_state(self, channel: int) -> bool:
        return False


# ---------------------------------------------------------------------------
# AWGDriverBase contract tests
# ---------------------------------------------------------------------------


def test_awg_driver_base_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        AWGDriverBase()  # type: ignore[abstract]


def test_awg_driver_base_incomplete_subclass_raises_on_instantiation() -> None:
    class _Incomplete(AWGDriverBase):
        def open(self) -> None:
            pass

        # all other abstract methods missing

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_awg_driver_base_complete_subclass_instantiates() -> None:
    driver = _MinimalAWGDriver()
    assert isinstance(driver, AWGDriverBase)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_output_load", (1, 50.0)),
        ("get_output_load", (1,)),
        ("align_phase", ()),
        ("set_modulation", (1, ModulationType.AM, Sine(frequency_hz=1000.0), 0.5)),
        ("modulation_enable", (1, True)),
    ],
)
def test_awg_driver_base_optional_methods_raise_not_implemented(
    method_name: str,
    args: tuple[object, ...],
) -> None:
    driver = _MinimalAWGDriver()
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for _MinimalAWGDriver"):
        getattr(driver, method_name)(*args)


# ---------------------------------------------------------------------------
# Waveform definitions — validation happens at definition time, before any I/O
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (lambda: Sine(frequency_hz=0.0), "frequency_hz must be positive"),
        (lambda: Sine(frequency_hz=-1000.0), "frequency_hz must be positive"),
        (lambda: Square(frequency_hz=1000.0, duty_cycle_pct=-1.0), "duty_cycle_pct must be between 0 and 100"),
        (lambda: Square(frequency_hz=1000.0, duty_cycle_pct=101.0), "duty_cycle_pct must be between 0 and 100"),
        (lambda: Sawtooth(frequency_hz=0.0), "frequency_hz must be positive"),
        (lambda: Triangle(frequency_hz=0.0), "frequency_hz must be positive"),
        (lambda: Pulse(frequency_hz=1000.0, width_s=0.0), "width_s must be positive"),
        (lambda: Pulse(frequency_hz=1000.0, width_s=0.001), r"width_s \+ delay_s must fit within the period"),
        (
            lambda: Pulse(frequency_hz=1000.0, width_s=0.0005, delay_s=0.0006),
            r"width_s \+ delay_s must fit within the period",
        ),
        (lambda: Pulse(frequency_hz=1000.0, width_s=0.0005, delay_s=-0.001), "delay_s must be non-negative"),
        (lambda: Arbitrary(samples=(0.5, 1.5), sample_rate_hz=1e6), r"samples must be normalized to \[-1.0, 1.0\]"),
        (lambda: Arbitrary(samples=(0.0, 1.0), sample_rate_hz=0.0), "sample_rate_hz must be positive"),
        (lambda: Arbitrary(samples=(), sample_rate_hz=1e6), "must contain at least 2 samples"),
        (lambda: Arbitrary(samples=(0.5,), sample_rate_hz=1e6), "must contain at least 2 samples"),
    ],
)
def test_waveform_definitions_reject_invalid_parameters(factory, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        factory()


@pytest.mark.parametrize("shape", [Sine, Square, Sawtooth, Triangle])
@pytest.mark.parametrize(
    ("phase_in", "expected"),
    [
        (0.0, 0.0),
        (90.0, 90.0),
        (-180.0, -180.0),
        (180.0, -180.0),
        (270.0, -90.0),
        (-190.0, 170.0),
        (720.0, 0.0),
    ],
)
def test_phase_deg_normalizes_at_construction(shape: type, phase_in: float, expected: float) -> None:
    """Phase wraps into [-180, 180) at definition time; +180 canonicalizes to -180."""
    assert shape(frequency_hz=1000.0, phase_deg=phase_in).phase_deg == expected


def test_waveform_definitions_are_immutable() -> None:
    wfm = Sine(frequency_hz=1000.0)
    with pytest.raises(FrozenInstanceError):
        wfm.frequency_hz = 2000.0  # type: ignore[misc]


def test_waveform_replace_supports_parameter_sweeps() -> None:
    wfm = Square(frequency_hz=1000.0, duty_cycle_pct=30.0)
    swept = replace(wfm, frequency_hz=2000.0)
    assert swept.frequency_hz == 2000.0
    assert swept.duty_cycle_pct == 30.0


def test_arbitrary_coerces_samples_to_tuple() -> None:
    wfm = Arbitrary(samples=[0.0, 0.5, -0.5], sample_rate_hz=1e6)  # type: ignore[arg-type]
    assert wfm.samples == (0.0, 0.5, -0.5)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_driver() -> MagicMock:
    driver = MagicMock(spec=AWGDriverBase)
    driver.get_waveform.return_value = Sine(frequency_hz=1000.0)
    driver.get_amplitude.return_value = (2.5, AmplitudeMeasurementUnit.VPP)
    driver.get_offset.return_value = 0.0
    driver.get_output_state.return_value = False
    driver.get_output_load.return_value = 50.0
    driver.get_modulation_state.return_value = (ModulationType.AM, False)
    return driver


@pytest.fixture
def awg(mock_driver: MagicMock) -> InstroAWG:
    return InstroAWG(name="test_awg", driver=mock_driver, num_channels=2)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_open_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.open()
    mock_driver.open.assert_called_once()


def test_close_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.close()
    mock_driver.close.assert_called_once()


@pytest.mark.parametrize("num_channels", [0, -1])
def test_init_raises_for_non_positive_num_channels(mock_driver: MagicMock, num_channels: int) -> None:
    with pytest.raises(ValueError, match="num_channels must be at least 1"):
        InstroAWG(name="test_awg", driver=mock_driver, num_channels=num_channels)


def test_start_raises_if_no_channel_configured(awg: InstroAWG) -> None:
    with pytest.raises(ValueError, match="set_waveform must be called for at least one channel"):
        awg.start()


def test_start_succeeds_with_one_of_two_channels_configured(awg: InstroAWG) -> None:
    """Unused channels must not block start(); the daemon's output-state poll needs no waveform."""
    awg.set_waveform(1, Sine(frequency_hz=1000.0))
    awg.start()
    try:
        assert awg._background_thread is not None
        assert awg._background_thread.is_alive()
    finally:
        awg.close()


def test_start_succeeds_once_every_channel_configured(awg: InstroAWG) -> None:
    awg.set_waveform(1, Sine(frequency_hz=1000.0))
    awg.set_waveform(2, Square(frequency_hz=1000.0))
    awg.start()
    try:
        assert awg._background_thread is not None
        assert awg._background_thread.is_alive()
    finally:
        awg.close()


def test_background_daemon_polls_only_output_state_per_channel(awg: InstroAWG) -> None:
    """Other readbacks are opt-in via add_background_daemon_function; a broader default would drain settings queries."""
    registered = [(method, kwargs) for method, _, kwargs in awg._background_methods]
    assert registered == [
        (awg.get_output_state, {"channel": 1}),
        (awg.get_output_state, {"channel": 2}),
    ]


# ---------------------------------------------------------------------------
# Error checking
# ---------------------------------------------------------------------------


def test_set_waveform_checks_errors_after_driver_call(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_waveform(1, Sine(frequency_hz=1000.0))
    mock_driver.check_errors.assert_called_once()


def test_set_waveform_check_errors_failure_leaves_channel_unconfigured(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Scope-style ordering: check_errors runs before config is recorded, so a rejected set is never cached."""
    mock_driver.check_errors.side_effect = RuntimeError("instrument error queue not empty")
    with pytest.raises(RuntimeError, match="instrument error queue not empty"):
        awg.set_waveform(1, Sine(frequency_hz=1000.0))
    assert 1 not in awg._channel_waveforms


def test_getters_check_errors(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Scope precedent: a pending error surfaces at the next query instead of hanging a later blocking read."""
    awg.get_output_state(1)
    awg.get_offset(1)
    awg.get_waveform(1)
    assert mock_driver.check_errors.call_count == 3


def test_getter_check_errors_failure_propagates(awg: InstroAWG, mock_driver: MagicMock) -> None:
    mock_driver.check_errors.side_effect = RuntimeError("instrument error queue not empty")
    with pytest.raises(RuntimeError, match="instrument error queue not empty"):
        awg.get_offset(1)


def test_setter_tags_may_reuse_helper_parameter_names(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Helper params are positional-only, so user tags named after them publish instead of colliding."""
    cmd = awg.set_offset(1, 0.5, channel_suffix="rig7", value="x")
    assert cmd.tags["channel_suffix"] == "rig7"
    assert cmd.tags["value"] == "x"
    mock_driver.set_offset.assert_called_once_with(1, 0.5)


def test_getter_tags_may_reuse_helper_parameter_names(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_offset(1, driver_method="tagged", channel_suffix="rig7")
    assert meas is not None
    assert meas.tags["driver_method"] == "tagged"
    assert meas.tags["channel_suffix"] == "rig7"


# ---------------------------------------------------------------------------
# set_waveform — the definition carries the shape; one atomic apply per channel
# ---------------------------------------------------------------------------


def test_set_waveform_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    wfm = Sine(frequency_hz=1000.0)
    awg.set_waveform(1, wfm)
    mock_driver.set_waveform.assert_called_once_with(channel=1, waveform=wfm)


def test_set_waveform_raises_for_non_waveform(awg: InstroAWG, mock_driver: MagicMock) -> None:
    with pytest.raises(TypeError, match="waveform must be a Waveform definition, got str"):
        awg.set_waveform(1, "SINE")  # type: ignore[arg-type]
    mock_driver.set_waveform.assert_not_called()


def test_set_waveform_rejects_waveform_subclass(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """A subclass would confuse driver isinstance dispatch and has no published name; reject before any I/O."""

    class _CustomSine(Sine):
        pass

    with pytest.raises(TypeError, match="waveform must be a Waveform definition, got _CustomSine"):
        awg.set_waveform(1, _CustomSine(frequency_hz=1000.0))
    mock_driver.set_waveform.assert_not_called()


def test_published_names_cover_every_waveform_definition() -> None:
    """A new definition added to the Waveform union must get a published name in the same change."""
    assert set(get_args(Waveform)) == set(_PUBLISHED_NAMES)


def test_set_waveform_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_waveform(1, Sine(frequency_hz=1000.0))
    assert "test_awg.ch1.waveform.cmd" in cmd.channel_data


def test_set_waveform_ch2_uses_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_waveform(2, Sawtooth(frequency_hz=1000.0))
    assert "test_awg.ch2.waveform.cmd" in cmd.channel_data


@pytest.mark.parametrize(
    ("waveform", "published"),
    [
        (Sine(frequency_hz=1000.0), "SINE"),
        (Square(frequency_hz=1000.0), "SQUARE"),
        (Sawtooth(frequency_hz=1000.0), "SAWTOOTH"),
        (Triangle(frequency_hz=1000.0), "TRIANGLE"),
        (Pulse(frequency_hz=1000.0, width_s=0.0005), "PULSE"),
        (Arbitrary(samples=(0.0, 1.0), sample_rate_hz=1e6), "ARBITRARY"),
        (StaticValue(value=1.5), "STATICVALUE"),
    ],
)
def test_set_waveform_publishes_type_name(
    awg: InstroAWG, mock_driver: MagicMock, waveform: Waveform, published: str
) -> None:
    cmd = awg.set_waveform(1, waveform)
    assert cmd.channel_data["test_awg.ch1.waveform.cmd"] == published


def test_set_waveform_ships_shape_parameters_as_tags(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_waveform(1, Square(frequency_hz=1000.0, duty_cycle_pct=30.0))
    assert cmd.tags["frequency_hz"] == "1000.0"
    assert cmd.tags["duty_cycle_pct"] == "30.0"
    assert cmd.tags["phase_deg"] == "0.0"


def test_set_waveform_arbitrary_never_publishes_samples(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Sample data can be megabytes; only the summary (count, rate) is published."""
    cmd = awg.set_waveform(1, Arbitrary(samples=(0.0, 0.5, -0.5), sample_rate_hz=1e6))
    assert cmd.tags["num_samples"] == "3"
    assert cmd.tags["sample_rate_hz"] == "1000000.0"
    assert "samples" not in cmd.tags
    assert list(cmd.channel_data) == ["test_awg.ch1.waveform.cmd"]


def test_set_waveform_records_definition_per_channel(awg: InstroAWG, mock_driver: MagicMock) -> None:
    sine = Sine(frequency_hz=1000.0)
    square = Square(frequency_hz=2000.0)
    awg.set_waveform(1, sine)
    awg.set_waveform(2, square)
    assert awg._channel_waveforms == {1: sine, 2: square}


def test_set_waveform_sweep_via_replace_delegates_updated_definition(awg: InstroAWG, mock_driver: MagicMock) -> None:
    wfm = Sine(frequency_hz=1000.0)
    awg.set_waveform(1, wfm)
    awg.set_waveform(1, replace(wfm, frequency_hz=2000.0))
    assert mock_driver.set_waveform.call_args.kwargs["waveform"] == Sine(frequency_hz=2000.0)


def test_get_waveform_returns_definition_not_measurement(awg: InstroAWG, mock_driver: MagicMock) -> None:
    result = awg.get_waveform(1)
    mock_driver.get_waveform.assert_called_once_with(channel=1)
    assert result == Sine(frequency_hz=1000.0)


# ---------------------------------------------------------------------------
# Companion numeric Command — NominalConnect drops the string-typed waveform
# Command, so shape parameters must land as their own all-float channels
# ---------------------------------------------------------------------------


def _awg_with_publisher(mock_driver: MagicMock) -> tuple[InstroAWG, MagicMock]:
    publisher = MagicMock()
    return InstroAWG(name="test_awg", driver=mock_driver, num_channels=2, publishers=[publisher]), publisher


def test_set_waveform_publishes_numeric_companion_for_shape_params(mock_driver: MagicMock) -> None:
    awg, publisher = _awg_with_publisher(mock_driver)
    returned = awg.set_waveform(1, Square(frequency_hz=1000.0, duty_cycle_pct=30.0))
    published = [call.args[0] for call in publisher.publish.call_args_list]
    companions = [p for p in published if "test_awg.ch1.frequency_hz.cmd" in p.channel_data]
    assert len(companions) == 1
    companion = companions[0]
    assert companion.channel_data["test_awg.ch1.frequency_hz.cmd"] == 1000.0
    assert companion.channel_data["test_awg.ch1.duty_cycle_pct.cmd"] == 30.0
    assert companion.channel_data["test_awg.ch1.phase_deg.cmd"] == 0.0
    assert all(isinstance(v, float) for v in companion.channel_data.values())
    assert companion.tags["waveform"] == "SQUARE"
    assert companion.timestamp == returned.timestamp


def test_set_waveform_publishes_both_type_and_companion_commands(mock_driver: MagicMock) -> None:
    awg, publisher = _awg_with_publisher(mock_driver)
    awg.set_waveform(1, Sine(frequency_hz=1000.0))
    published = [call.args[0] for call in publisher.publish.call_args_list]
    assert len(published) == 2
    assert any(p.channel_data.get("test_awg.ch1.waveform.cmd") == "SINE" for p in published)


def test_set_waveform_arbitrary_companion_summarizes_samples(mock_driver: MagicMock) -> None:
    awg, publisher = _awg_with_publisher(mock_driver)
    awg.set_waveform(1, Arbitrary(samples=(0.0, 0.5, -0.5), sample_rate_hz=1e6))
    published = [call.args[0] for call in publisher.publish.call_args_list]
    companions = [p for p in published if "test_awg.ch1.sample_rate_hz.cmd" in p.channel_data]
    assert len(companions) == 1
    companion = companions[0]
    assert companion.channel_data["test_awg.ch1.num_samples.cmd"] == 3.0
    assert "test_awg.ch1.samples.cmd" not in companion.channel_data
    assert companion.tags["waveform"] == "ARBITRARY"


# ---------------------------------------------------------------------------
# Amplitude
# ---------------------------------------------------------------------------


def test_set_amplitude_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VPP)
    mock_driver.set_amplitude.assert_called_once_with(channel=1, amplitude=2.5, unit=AmplitudeMeasurementUnit.VPP)


def test_set_amplitude_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VPP)
    assert "test_awg.ch1.amplitude.cmd" in cmd.channel_data


def test_set_amplitude_ships_unit_as_tag(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """A bare amplitude is unit-ambiguous (2.5 Vpp vs Vrms vs dBm); the unit tags the same Command."""
    cmd = awg.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VRMS)
    assert cmd.channel_data["test_awg.ch1.amplitude.cmd"] == 2.5
    assert cmd.tags["unit"] == "VRMS"


def test_set_amplitude_channel_data_stays_float_only(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_amplitude(1, 2.5, AmplitudeMeasurementUnit.VPP)
    assert list(cmd.channel_data) == ["test_awg.ch1.amplitude.cmd"]
    assert all(isinstance(v, float) for v in cmd.channel_data.values())


def test_set_amplitude_raises_for_non_enum_unit(awg: InstroAWG, mock_driver: MagicMock) -> None:
    with pytest.raises(TypeError, match="unit must be an AmplitudeMeasurementUnit, got str"):
        awg.set_amplitude(1, 2.5, "VRMS")  # type: ignore[arg-type]
    mock_driver.set_amplitude.assert_not_called()


def test_get_amplitude_returns_tuple_not_measurement(awg: InstroAWG, mock_driver: MagicMock) -> None:
    result = awg.get_amplitude(1)
    mock_driver.get_amplitude.assert_called_once_with(channel=1)
    assert result == (2.5, AmplitudeMeasurementUnit.VPP)


# ---------------------------------------------------------------------------
# convert_amplitude — pure waveform-aware unit conversion (types.py)
# ---------------------------------------------------------------------------


def test_convert_amplitude_identity_returns_value_unchanged() -> None:
    value = convert_amplitude(5.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VPP, Sine(frequency_hz=1e3))
    assert value == 5.0


@pytest.mark.parametrize(
    "waveform",
    [
        Sine(frequency_hz=1e3),
        Square(frequency_hz=1e3, duty_cycle_pct=10.0),
        Sawtooth(frequency_hz=1e3),
        Triangle(frequency_hz=1e3),
        Pulse(frequency_hz=1e3, width_s=1e-6),
        StaticValue(value=1.0),
        Arbitrary(samples=(0.5, -0.5, 0.25), sample_rate_hz=1e6),
    ],
)
def test_convert_amplitude_vpp_vp_is_exactly_double_regardless_of_shape(waveform: Waveform) -> None:
    vp = convert_amplitude(2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VP, waveform)
    assert vp == pytest.approx(1.0)
    vpp = convert_amplitude(vp, AmplitudeMeasurementUnit.VP, AmplitudeMeasurementUnit.VPP, waveform)
    assert vpp == pytest.approx(2.0)


def test_convert_amplitude_sine_vrms_uses_root_two_crest_factor() -> None:
    vrms = convert_amplitude(2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS, Sine(frequency_hz=1e3))
    assert vrms == pytest.approx(1.0 / math.sqrt(2))


@pytest.mark.parametrize("duty_cycle_pct", [10.0, 50.0, 90.0])
def test_convert_amplitude_square_vrms_is_independent_of_duty_cycle(duty_cycle_pct: float) -> None:
    """A bipolar two-level signal has constant magnitude, so RMS doesn't depend on duty cycle."""
    waveform = Square(frequency_hz=1e3, duty_cycle_pct=duty_cycle_pct)
    vrms = convert_amplitude(2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS, waveform)
    assert vrms == pytest.approx(1.0)


@pytest.mark.parametrize("width_s", [1e-7, 4e-4])
def test_convert_amplitude_pulse_vrms_is_independent_of_width(width_s: float) -> None:
    waveform = Pulse(frequency_hz=1e3, width_s=width_s)
    vrms = convert_amplitude(2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS, waveform)
    assert vrms == pytest.approx(1.0)


@pytest.mark.parametrize("shape", [Sawtooth, Triangle])
def test_convert_amplitude_ramp_shapes_use_root_three_crest_factor(shape: type) -> None:
    vrms = convert_amplitude(2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS, shape(frequency_hz=1e3))
    assert vrms == pytest.approx(1.0 / math.sqrt(3))


def test_convert_amplitude_static_value_vrms_equals_vp() -> None:
    vrms = convert_amplitude(2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS, StaticValue(value=1.0))
    assert vrms == pytest.approx(1.0)


def test_convert_amplitude_arbitrary_derives_crest_factor_from_samples() -> None:
    """Two-level samples behave like Square: RMS equals the peak regardless of duty."""
    waveform = Arbitrary(samples=(1.0, 1.0, -1.0), sample_rate_hz=1e6)
    vrms = convert_amplitude(2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS, waveform)
    assert vrms == pytest.approx(1.0)


def test_convert_amplitude_arbitrary_all_zero_samples_does_not_raise() -> None:
    waveform = Arbitrary(samples=(0.0, 0.0, 0.0, 0.0), sample_rate_hz=1e6)
    vrms = convert_amplitude(0.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS, waveform)
    assert vrms == 0.0


def test_convert_amplitude_dbm_matches_known_reference() -> None:
    """1 Vrms into 50 ohms is the well-known ~13.01 dBm reference point."""
    dbm = convert_amplitude(
        1.0, AmplitudeMeasurementUnit.VRMS, AmplitudeMeasurementUnit.DBM, Sine(frequency_hz=1e3), impedance_ohms=50.0
    )
    assert dbm == pytest.approx(10.0 * math.log10(20.0))


def test_convert_amplitude_dbm_round_trips_back_to_vrms() -> None:
    waveform = Sine(frequency_hz=1e3)
    dbm = convert_amplitude(
        1.0, AmplitudeMeasurementUnit.VRMS, AmplitudeMeasurementUnit.DBM, waveform, impedance_ohms=50.0
    )
    vrms = convert_amplitude(
        dbm, AmplitudeMeasurementUnit.DBM, AmplitudeMeasurementUnit.VRMS, waveform, impedance_ohms=50.0
    )
    assert vrms == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("from_unit", "to_unit"),
    [
        (AmplitudeMeasurementUnit.DBM, AmplitudeMeasurementUnit.VRMS),
        (AmplitudeMeasurementUnit.VRMS, AmplitudeMeasurementUnit.DBM),
    ],
)
def test_convert_amplitude_dbm_requires_impedance(
    from_unit: AmplitudeMeasurementUnit, to_unit: AmplitudeMeasurementUnit
) -> None:
    with pytest.raises(ValueError, match="impedance_ohms is required"):
        convert_amplitude(1.0, from_unit, to_unit, Sine(frequency_hz=1e3))


@pytest.mark.parametrize("impedance_ohms", [0.0, -50.0])
def test_convert_amplitude_dbm_rejects_non_positive_impedance(impedance_ohms: float) -> None:
    with pytest.raises(ValueError, match="impedance_ohms must be positive"):
        convert_amplitude(
            1.0,
            AmplitudeMeasurementUnit.VRMS,
            AmplitudeMeasurementUnit.DBM,
            Sine(frequency_hz=1e3),
            impedance_ohms=impedance_ohms,
        )


def test_convert_amplitude_dbm_target_rejects_zero_amplitude() -> None:
    with pytest.raises(ValueError, match="amplitude must be positive to convert to DBM"):
        convert_amplitude(
            0.0,
            AmplitudeMeasurementUnit.VPP,
            AmplitudeMeasurementUnit.DBM,
            Sine(frequency_hz=1e3),
            impedance_ohms=50.0,
        )


def test_convert_amplitude_rejects_non_enum_from_unit() -> None:
    with pytest.raises(TypeError, match="from_unit must be an AmplitudeMeasurementUnit, got str"):
        convert_amplitude(1.0, "VPP", AmplitudeMeasurementUnit.VRMS, Sine(frequency_hz=1e3))  # type: ignore[arg-type]


def test_convert_amplitude_rejects_non_enum_to_unit() -> None:
    with pytest.raises(TypeError, match="to_unit must be an AmplitudeMeasurementUnit, got str"):
        convert_amplitude(1.0, AmplitudeMeasurementUnit.VPP, "VRMS", Sine(frequency_hz=1e3))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# InstroAWG.convert_amplitude — channel-aware wrapper
# ---------------------------------------------------------------------------


def test_instroawg_convert_amplitude_uses_channels_configured_waveform(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_waveform(1, Sine(frequency_hz=1e3))
    vrms = awg.convert_amplitude(1, 2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS)
    assert vrms == pytest.approx(1.0 / math.sqrt(2))


def test_instroawg_convert_amplitude_raises_if_waveform_not_configured(awg: InstroAWG) -> None:
    with pytest.raises(ValueError, match="channel 1 has no waveform configured"):
        awg.convert_amplitude(1, 2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS)


def test_instroawg_convert_amplitude_dbm_uses_output_load_when_not_given(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    awg.set_waveform(1, Sine(frequency_hz=1e3))
    mock_driver.get_output_load.return_value = 50.0
    dbm = awg.convert_amplitude(1, 1.0, AmplitudeMeasurementUnit.VRMS, AmplitudeMeasurementUnit.DBM)
    mock_driver.get_output_load.assert_called_once_with(channel=1)
    assert dbm == pytest.approx(10.0 * math.log10(20.0))


def test_instroawg_convert_amplitude_dbm_explicit_impedance_skips_output_load_lookup(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    awg.set_waveform(1, Sine(frequency_hz=1e3))
    awg.convert_amplitude(1, 1.0, AmplitudeMeasurementUnit.VRMS, AmplitudeMeasurementUnit.DBM, impedance_ohms=75.0)
    mock_driver.get_output_load.assert_not_called()


def test_instroawg_convert_amplitude_dbm_raises_if_output_load_is_high_z(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    awg.set_waveform(1, Sine(frequency_hz=1e3))
    mock_driver.get_output_load.return_value = None
    with pytest.raises(ValueError, match="channel 1 has no known output load"):
        awg.convert_amplitude(1, 1.0, AmplitudeMeasurementUnit.VRMS, AmplitudeMeasurementUnit.DBM)


def test_instroawg_convert_amplitude_dbm_raises_if_driver_does_not_support_output_load(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    awg.set_waveform(1, Sine(frequency_hz=1e3))
    mock_driver.get_output_load.side_effect = NotImplementedError("get_output_load is not implemented")
    with pytest.raises(ValueError, match="channel 1 has no known output load"):
        awg.convert_amplitude(1, 1.0, AmplitudeMeasurementUnit.VRMS, AmplitudeMeasurementUnit.DBM)


def test_instroawg_convert_amplitude_rejects_non_enum_unit(awg: InstroAWG) -> None:
    awg.set_waveform(1, Sine(frequency_hz=1e3))
    with pytest.raises(TypeError, match="from_unit must be an AmplitudeMeasurementUnit, got str"):
        awg.convert_amplitude(1, 2.0, "VPP", AmplitudeMeasurementUnit.VRMS)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Offset, output enable, load, phase alignment
# ---------------------------------------------------------------------------


def test_set_offset_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_offset(1, 0.5)
    mock_driver.set_offset.assert_called_once_with(1, 0.5)


def test_set_offset_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_offset(1, 0.5)
    assert "test_awg.ch1.offset.cmd" in cmd.channel_data


def test_output_enable_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.output_enable(1, True)
    mock_driver.output_enable.assert_called_once_with(1, True)


def test_output_enable_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.output_enable(1, True)
    assert "test_awg.ch1.enabled.cmd" in cmd.channel_data


def test_output_enable_does_not_create_channel_waveform(awg: InstroAWG) -> None:
    """Config presence gates start(); commands that don't require a waveform must not fake one."""
    awg.output_enable(1, True)
    assert 1 not in awg._channel_waveforms


def test_set_output_load_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_output_load(1, 50.0)
    mock_driver.set_output_load.assert_called_once_with(channel=1, load=50.0)


def test_set_output_load_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_output_load(1, 50.0)
    assert "test_awg.ch1.load.cmd" in cmd.channel_data


def test_set_output_load_high_z_passes_none_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_output_load(1, None)
    mock_driver.set_output_load.assert_called_once_with(channel=1, load=None)


def test_set_output_load_high_z_publishes_float_inf(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Command and readback channels both publish float('inf') so ch{n}.load.cmd stays single-typed."""
    cmd = awg.set_output_load(1, None)
    assert cmd.channel_data["test_awg.ch1.load.cmd"] == float("inf")


def test_align_phase_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.align_phase()
    mock_driver.align_phase.assert_called_once()


def test_align_phase_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.align_phase()
    assert "test_awg.phase.align.cmd" in cmd.channel_data


def test_set_modulation_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    shape = Sine(frequency_hz=1000.0)
    awg.set_modulation(1, ModulationType.AM, shape, 0.5)
    mock_driver.set_modulation.assert_called_once_with(
        channel=1, mod_type=ModulationType.AM, shape=shape, magnitude=0.5
    )


def test_set_modulation_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_modulation(1, ModulationType.FM, Sine(frequency_hz=1000.0), 0.5)
    assert "test_awg.ch1.modulation.cmd" in cmd.channel_data


def test_set_modulation_raises_for_non_enum_mod_type(awg: InstroAWG, mock_driver: MagicMock) -> None:
    with pytest.raises(TypeError, match="mod_type must be a ModulationType, got str"):
        awg.set_modulation(1, "AM", Sine(frequency_hz=1000.0), 0.5)  # type: ignore[arg-type]
    mock_driver.set_modulation.assert_not_called()


def test_modulation_enable_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.modulation_enable(1, True)
    mock_driver.modulation_enable.assert_called_once_with(channel=1, enable=True)


def test_modulation_enable_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.modulation_enable(1, True)
    assert "test_awg.ch1.modulation_enabled.cmd" in cmd.channel_data


def test_modulation_enable_false_publishes_off(awg: InstroAWG, mock_driver: MagicMock) -> None:
    # _package_command coerces bool to float (see InstroAWG.output_enable's identical pattern).
    cmd = awg.modulation_enable(1, False)
    assert cmd.channel_data["test_awg.ch1.modulation_enabled.cmd"] == 0.0


def test_modulation_enable_true_publishes_on(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.modulation_enable(1, True)
    assert cmd.channel_data["test_awg.ch1.modulation_enabled.cmd"] == 1.0


def test_get_modulation_state_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.get_modulation_state(1)
    mock_driver.get_modulation_state.assert_called_once_with(channel=1)


def test_get_modulation_state_returns_measurement_with_correct_descriptor(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    mock_driver.get_modulation_state.return_value = (ModulationType.AM, True)
    meas = awg.get_modulation_state(1)
    assert meas is not None
    assert meas.channel_data["test_awg.ch1.modulation_state"] == [1.0]


def test_get_modulation_state_ships_type_as_tag(awg: InstroAWG, mock_driver: MagicMock) -> None:
    mock_driver.get_modulation_state.return_value = (ModulationType.FM, False)
    meas = awg.get_modulation_state(1)
    assert meas is not None
    assert meas.channel_data["test_awg.ch1.modulation_state"] == [0.0]
    assert meas.tags["mod_type"] == "FM"


# ---------------------------------------------------------------------------
# Measurement getters
# ---------------------------------------------------------------------------


def test_get_offset_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.get_offset(1)
    mock_driver.get_offset.assert_called_once_with(channel=1)


def test_get_offset_returns_measurement_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_offset(1)
    assert meas is not None
    assert "test_awg.ch1.offset" in meas.channel_data
    assert meas.channel_data["test_awg.ch1.offset"] == [0.0]


def test_get_output_state_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.get_output_state(1)
    mock_driver.get_output_state.assert_called_once_with(channel=1)


def test_get_output_state_returns_measurement_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_output_state(1)
    assert meas is not None
    assert "test_awg.ch1.enabled" in meas.channel_data


def test_get_output_load_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.get_output_load(1)
    mock_driver.get_output_load.assert_called_once_with(channel=1)


def test_get_output_load_returns_measurement_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_output_load(1)
    assert meas is not None
    assert "test_awg.ch1.load" in meas.channel_data
    assert meas.channel_data["test_awg.ch1.load"] == [50.0]


def test_get_output_load_high_z_publishes_float_inf(awg: InstroAWG, mock_driver: MagicMock) -> None:
    mock_driver.get_output_load.return_value = None
    meas = awg.get_output_load(1)
    assert meas is not None
    assert meas.channel_data["test_awg.ch1.load"] == [float("inf")]


# ---------------------------------------------------------------------------
# Channel range validation — `awg` fixture is num_channels=2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_waveform", (Sine(frequency_hz=1000.0),)),
        ("get_waveform", ()),
        ("set_amplitude", (2.5, AmplitudeMeasurementUnit.VPP)),
        ("get_amplitude", ()),
        ("convert_amplitude", (2.5, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS)),
        ("set_offset", (0.5,)),
        ("get_offset", ()),
        ("output_enable", (True,)),
        ("get_output_state", ()),
        ("set_output_load", (50.0,)),
        ("get_output_load", ()),
        ("set_modulation", (ModulationType.AM, Sine(frequency_hz=1000.0), 0.5)),
        ("modulation_enable", (True,)),
    ],
)
@pytest.mark.parametrize("channel", [0, 3])
def test_channel_scoped_methods_raise_for_out_of_range_channel(
    awg: InstroAWG, method_name: str, args: tuple[object, ...], channel: int
) -> None:
    with pytest.raises(ValueError, match=f"channel {channel} out of range"):
        getattr(awg, method_name)(channel, *args)


def test_channel_at_lower_bound_is_valid(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_waveform(1, Sine(frequency_hz=1000.0))


def test_channel_at_upper_bound_is_valid(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_waveform(2, Sine(frequency_hz=1000.0))
