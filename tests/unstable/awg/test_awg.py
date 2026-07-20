"""Tests for AWGDriverBase contract and InstroAWG composition."""

from unittest.mock import MagicMock

import pytest

from instro.unstable.awg.awg import AWGDriverBase, InstroAWG
from instro.unstable.awg.types import VoltageUnit, WaveformType

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

    def set_std_waveform(self, channel: int, waveform: WaveformType) -> None:
        pass

    def get_std_waveform(self, channel: int) -> WaveformType:
        return WaveformType.SINE

    def set_std_frequency(self, channel: int, frequency: float) -> None:
        pass

    def get_std_frequency(self, channel: int) -> float:
        return 1000.0

    def set_std_amplitude(self, channel: int, amplitude: float, unit: VoltageUnit) -> None:
        pass

    def get_std_amplitude(self, channel: int) -> tuple[float, VoltageUnit]:
        return (1.0, VoltageUnit.VPP)

    def set_std_offset(self, channel: int, offset: float) -> None:
        pass

    def get_std_offset(self, channel: int) -> float:
        return 0.0

    def output_enable(self, channel: int, enable: bool) -> None:
        pass

    def get_output_state(self, channel: int) -> bool:
        return False

    def set_std_output_load(self, channel: int, load: float | None) -> None:
        pass

    def get_std_output_load(self, channel: int) -> float | None:
        return 50.0

    def set_phase(self, channel: int, phase_deg: float) -> None:
        pass

    def get_phase(self, channel: int) -> float:
        return 0.0


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
        ("align_phase", ()),
        ("set_voltage_unit", (1, VoltageUnit.VPP)),
        ("get_voltage_unit", (1,)),
        ("set_high_level", (1, 1.0)),
        ("set_low_level", (1, 0.0)),
        ("set_square_duty_cycle", (1, 50.0)),
        ("set_ramp_symmetry", (1, 50.0)),
        ("set_pulse_width", (1, 0.001)),
        ("set_pulse_delay", (1, 0.001)),
    ],
)
def test_awg_driver_base_optional_methods_raise_not_implemented(
    method_name: str,
    args: tuple[object, ...],
) -> None:
    driver = _MinimalAWGDriver()
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for _MinimalAWGDriver"):
        getattr(driver, method_name)(*args)


def test_awg_driver_base_pulse_phase_unsupported_by_default() -> None:
    assert _MinimalAWGDriver().supports_pulse_phase is False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_driver() -> MagicMock:
    driver = MagicMock(spec=AWGDriverBase)
    # spec mocks return truthy Mocks for attributes; pin the contract default
    driver.supports_pulse_phase = False
    driver.get_std_waveform.return_value = WaveformType.SINE
    driver.get_std_frequency.return_value = 1000.0
    driver.get_std_amplitude.return_value = (2.5, VoltageUnit.VPP)
    driver.get_std_offset.return_value = 0.0
    driver.get_output_state.return_value = False
    driver.get_std_output_load.return_value = 50.0
    driver.get_phase.return_value = 0.0
    driver.get_voltage_unit.return_value = VoltageUnit.VPP
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


def test_set_std_waveform_raises_for_non_enum_waveform(awg: InstroAWG, mock_driver: MagicMock) -> None:
    with pytest.raises(TypeError, match="waveform must be a WaveformType, got str"):
        awg.set_std_waveform(1, "SINE")  # type: ignore[arg-type]
    mock_driver.set_std_waveform.assert_not_called()


def test_start_raises_if_no_channel_configured(awg: InstroAWG) -> None:
    with pytest.raises(ValueError, match=r"set_std_waveform must be called for channel\(s\) 1, 2"):
        awg.start()


def test_start_raises_if_one_channel_unconfigured(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    with pytest.raises(ValueError, match=r"set_std_waveform must be called for channel\(s\) 2"):
        awg.start()
    awg.close()


def test_start_succeeds_once_every_channel_configured(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_std_waveform(2, WaveformType.SQUARE)
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


def test_set_std_waveform_checks_errors_after_driver_call(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    mock_driver.check_errors.assert_called_once()


def test_set_std_waveform_check_errors_failure_leaves_channel_unconfigured(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    """Scope-style ordering: check_errors runs before config is recorded, so a rejected set is never cached."""
    mock_driver.check_errors.side_effect = RuntimeError("instrument error queue not empty")
    with pytest.raises(RuntimeError, match="instrument error queue not empty"):
        awg.set_std_waveform(1, WaveformType.SINE)
    mock_driver.check_errors.side_effect = None
    with pytest.raises(ValueError, match="set_std_waveform must be called for channel 1"):
        awg.set_std_frequency(1, 5000.0)


def test_getters_check_errors(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Scope precedent: a pending error surfaces at the next query instead of hanging a later blocking read."""
    awg.get_output_state(1)
    awg.get_std_offset(1)
    awg.get_std_waveform(1)
    assert mock_driver.check_errors.call_count == 3


def test_getter_check_errors_failure_propagates(awg: InstroAWG, mock_driver: MagicMock) -> None:
    mock_driver.check_errors.side_effect = RuntimeError("instrument error queue not empty")
    with pytest.raises(RuntimeError, match="instrument error queue not empty"):
        awg.get_std_offset(1)


def test_setter_tags_may_reuse_helper_parameter_names(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Helper params are positional-only, so user tags named after them publish instead of colliding."""
    cmd = awg.set_std_offset(1, 0.5, validate="pre-flight", channel_suffix="rig7", value="x")
    assert cmd.tags["validate"] == "pre-flight"
    assert cmd.tags["channel_suffix"] == "rig7"
    assert cmd.tags["value"] == "x"
    mock_driver.set_std_offset.assert_called_once_with(1, 0.5)


def test_getter_tags_may_reuse_helper_parameter_names(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_std_offset(1, driver_method="tagged", channel_suffix="rig7")
    assert meas is not None
    assert meas.tags["driver_method"] == "tagged"
    assert meas.tags["channel_suffix"] == "rig7"


# ---------------------------------------------------------------------------
# Command setters — delegate to driver and return Command with correct descriptor
# ---------------------------------------------------------------------------


def test_set_std_waveform_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    mock_driver.set_std_waveform.assert_called_once_with(channel=1, waveform=WaveformType.SINE)


def test_set_std_waveform_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_waveform(1, WaveformType.SINE)
    assert "test_awg.ch1.waveform.cmd" in cmd.channel_data


def test_set_std_waveform_publishes_string_value_not_enum(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_waveform(1, WaveformType.SQUARE)
    assert cmd.channel_data["test_awg.ch1.waveform.cmd"] == "SQUARE"


def test_set_std_waveform_ch2_uses_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_waveform(2, WaveformType.RAMP)
    assert "test_awg.ch2.waveform.cmd" in cmd.channel_data


def test_set_std_frequency_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_std_frequency(1, 5000.0)
    mock_driver.set_std_frequency.assert_called_once_with(channel=1, frequency=5000.0)


def test_set_std_frequency_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    cmd = awg.set_std_frequency(1, 5000.0)
    assert "test_awg.ch1.frequency.cmd" in cmd.channel_data


def test_set_std_frequency_raises_if_channel_not_configured(awg: InstroAWG) -> None:
    with pytest.raises(ValueError, match="set_std_waveform"):
        awg.set_std_frequency(1, 5000.0)


def test_set_std_frequency_raises_for_dc_waveform(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.DC)
    with pytest.raises(ValueError, match="not valid for channel 1"):
        awg.set_std_frequency(1, 5000.0)


def test_set_std_amplitude_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_std_amplitude(1, 2.5, VoltageUnit.VPP)
    mock_driver.set_std_amplitude.assert_called_once_with(channel=1, amplitude=2.5, unit=VoltageUnit.VPP)


def test_set_std_amplitude_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    cmd = awg.set_std_amplitude(1, 2.5, VoltageUnit.VPP)
    assert "test_awg.ch1.amplitude.cmd" in cmd.channel_data


def test_set_std_amplitude_ships_unit_as_tag(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """A bare amplitude is unit-ambiguous (2.5 Vpp vs Vrms vs dBm); the unit tags the same Command."""
    awg.set_std_waveform(1, WaveformType.SINE)
    cmd = awg.set_std_amplitude(1, 2.5, VoltageUnit.VRMS)
    assert cmd.channel_data["test_awg.ch1.amplitude.cmd"] == 2.5
    assert cmd.tags["unit"] == "VRMS"


def test_set_std_amplitude_channel_data_stays_float_only(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """NominalConnect drops any Command whose channel_data holds a string; the unit must never be a channel."""
    awg.set_std_waveform(1, WaveformType.SINE)
    cmd = awg.set_std_amplitude(1, 2.5, VoltageUnit.VPP)
    assert list(cmd.channel_data) == ["test_awg.ch1.amplitude.cmd"]
    assert all(isinstance(v, float) for v in cmd.channel_data.values())


def test_set_std_amplitude_raises_for_non_enum_unit(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    with pytest.raises(TypeError, match="unit must be a VoltageUnit, got str"):
        awg.set_std_amplitude(1, 2.5, "VRMS")  # type: ignore[arg-type]
    mock_driver.set_std_amplitude.assert_not_called()


def test_set_voltage_unit_raises_for_non_enum_unit(awg: InstroAWG, mock_driver: MagicMock) -> None:
    with pytest.raises(TypeError, match="unit must be a VoltageUnit, got str"):
        awg.set_voltage_unit(1, "VRMS")  # type: ignore[arg-type]
    mock_driver.set_voltage_unit.assert_not_called()


def test_set_std_amplitude_raises_for_dc_waveform(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.DC)
    with pytest.raises(ValueError, match="not valid for channel 1"):
        awg.set_std_amplitude(1, 2.5, VoltageUnit.VPP)


def test_set_std_amplitude_allowed_for_noise_waveform(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.NOISE)
    awg.set_std_amplitude(1, 2.5, VoltageUnit.VPP)
    mock_driver.set_std_amplitude.assert_called_once_with(channel=1, amplitude=2.5, unit=VoltageUnit.VPP)


def test_set_std_offset_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_offset(1, 0.5)
    mock_driver.set_std_offset.assert_called_once_with(1, 0.5)


def test_set_std_offset_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_offset(1, 0.5)
    assert "test_awg.ch1.offset.cmd" in cmd.channel_data


def test_output_enable_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.output_enable(1, True)
    mock_driver.output_enable.assert_called_once_with(channel=1, enable=True)


def test_output_enable_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.output_enable(1, True)
    assert "test_awg.ch1.enabled.cmd" in cmd.channel_data


def test_set_std_output_load_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_output_load(1, 50.0)
    mock_driver.set_std_output_load.assert_called_once_with(channel=1, load=50.0)


def test_set_std_output_load_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_output_load(1, 50.0)
    assert "test_awg.ch1.load.cmd" in cmd.channel_data


def test_set_std_output_load_high_z_passes_none_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_output_load(1, None)
    mock_driver.set_std_output_load.assert_called_once_with(channel=1, load=None)


def test_set_std_output_load_high_z_publishes_float_inf(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Command and readback channels both publish float('inf') so ch{n}.load.cmd stays single-typed."""
    cmd = awg.set_std_output_load(1, None)
    assert cmd.channel_data["test_awg.ch1.load.cmd"] == float("inf")


def test_set_phase_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_phase(1, 90.0)
    mock_driver.set_phase.assert_called_once_with(1, 90.0)


def test_set_phase_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    cmd = awg.set_phase(1, 90.0)
    assert "test_awg.ch1.phase.cmd" in cmd.channel_data


def test_set_phase_raises_for_noise_waveform(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.NOISE)
    with pytest.raises(ValueError, match="not valid for channel 1"):
        awg.set_phase(1, 90.0)


def test_set_phase_raises_for_pulse_when_driver_lacks_support(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.PULSE)
    with pytest.raises(ValueError, match="set_phase is not supported for PULSE.*set_pulse_delay"):
        awg.set_phase(1, 90.0)
    mock_driver.set_phase.assert_not_called()


def test_set_phase_allowed_for_pulse_when_driver_supports_it(awg: InstroAWG, mock_driver: MagicMock) -> None:
    mock_driver.supports_pulse_phase = True
    awg.set_std_waveform(1, WaveformType.PULSE)
    awg.set_phase(1, 90.0)
    mock_driver.set_phase.assert_called_once_with(1, 90.0)


def test_align_phase_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.align_phase()
    mock_driver.align_phase.assert_called_once()


def test_align_phase_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.align_phase()
    assert "test_awg.phase.align.cmd" in cmd.channel_data


def test_set_voltage_unit_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_voltage_unit(1, VoltageUnit.VRMS)
    mock_driver.set_voltage_unit.assert_called_once_with(channel=1, unit=VoltageUnit.VRMS)


def test_set_voltage_unit_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_voltage_unit(1, VoltageUnit.VRMS)
    assert "test_awg.ch1.voltage_unit.cmd" in cmd.channel_data


def test_set_high_level_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_high_level(1, 3.3)
    mock_driver.set_high_level.assert_called_once_with(1, 3.3)


def test_set_high_level_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    cmd = awg.set_high_level(1, 3.3)
    assert "test_awg.ch1.high_level.cmd" in cmd.channel_data


def test_set_high_level_raises_for_dc_waveform(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.DC)
    with pytest.raises(ValueError, match="set_high_level is not valid for channel 1"):
        awg.set_high_level(1, 3.3)


def test_set_high_level_error_names_itself_when_unconfigured(awg: InstroAWG) -> None:
    with pytest.raises(ValueError, match="before set_high_level"):
        awg.set_high_level(1, 3.3)


def test_set_low_level_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_low_level(1, 0.0)
    mock_driver.set_low_level.assert_called_once_with(1, 0.0)


def test_set_low_level_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    cmd = awg.set_low_level(1, 0.0)
    assert "test_awg.ch1.low_level.cmd" in cmd.channel_data


def test_set_low_level_raises_for_dc_waveform(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.DC)
    with pytest.raises(ValueError, match="set_low_level is not valid for channel 1"):
        awg.set_low_level(1, 0.0)


# ---------------------------------------------------------------------------
# Waveform-specific optional setters
# ---------------------------------------------------------------------------


def test_set_square_duty_cycle_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SQUARE)
    awg.set_square_duty_cycle(1, 50.0)
    mock_driver.set_square_duty_cycle.assert_called_once_with(1, 50.0)


def test_set_square_duty_cycle_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SQUARE)
    cmd = awg.set_square_duty_cycle(1, 50.0)
    assert "test_awg.ch1.square.duty_cycle.cmd" in cmd.channel_data


def test_set_square_duty_cycle_raises_for_sine_waveform(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    with pytest.raises(ValueError, match="not valid for channel 1"):
        awg.set_square_duty_cycle(1, 50.0)


def test_set_ramp_symmetry_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.RAMP)
    awg.set_ramp_symmetry(1, 75.0)
    mock_driver.set_ramp_symmetry.assert_called_once_with(1, 75.0)


def test_set_ramp_symmetry_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.RAMP)
    cmd = awg.set_ramp_symmetry(1, 75.0)
    assert "test_awg.ch1.ramp.symmetry.cmd" in cmd.channel_data


def test_set_ramp_symmetry_raises_for_sine_waveform(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    with pytest.raises(ValueError, match="not valid for channel 1"):
        awg.set_ramp_symmetry(1, 75.0)


def test_set_pulse_width_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.PULSE)
    awg.set_pulse_width(1, 0.001)
    mock_driver.set_pulse_width.assert_called_once_with(1, 0.001)


def test_set_pulse_width_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.PULSE)
    cmd = awg.set_pulse_width(1, 0.001)
    assert "test_awg.ch1.pulse.width.cmd" in cmd.channel_data


def test_set_pulse_width_raises_for_sine_waveform(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    with pytest.raises(ValueError, match="not valid for channel 1"):
        awg.set_pulse_width(1, 0.001)


def test_set_pulse_delay_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.PULSE)
    awg.set_pulse_delay(1, 0.002)
    mock_driver.set_pulse_delay.assert_called_once_with(1, 0.002)


def test_set_pulse_delay_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.PULSE)
    cmd = awg.set_pulse_delay(1, 0.002)
    assert "test_awg.ch1.pulse.delay.cmd" in cmd.channel_data


def test_set_pulse_delay_raises_for_sine_waveform(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    with pytest.raises(ValueError, match="not valid for channel 1"):
        awg.set_pulse_delay(1, 0.002)


def test_channel_config_is_independent_per_channel(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Configuring channel 1 as SQUARE must not make channel 2 valid for duty cycle."""
    awg.set_std_waveform(1, WaveformType.SQUARE)
    with pytest.raises(ValueError, match="set_std_waveform must be called for channel 2"):
        awg.set_square_duty_cycle(2, 50.0)


# ---------------------------------------------------------------------------
# Channel config state tracking
# ---------------------------------------------------------------------------


def test_channel_config_starts_with_only_waveform_set(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    config = awg._channel_config[1]
    assert config.waveform is WaveformType.SINE
    assert config.voltage_unit is None
    assert config.output_enabled is None
    assert config.frequency_hz is None


def test_set_std_frequency_records_frequency_in_channel_config(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_std_frequency(1, 5000.0)
    assert awg._channel_config[1].frequency_hz == 5000.0


def test_set_std_amplitude_records_voltage_unit_in_channel_config(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_std_amplitude(1, 2.5, VoltageUnit.VRMS)
    assert awg._channel_config[1].voltage_unit is VoltageUnit.VRMS


def test_set_voltage_unit_records_unit_in_channel_config(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_voltage_unit(1, VoltageUnit.DBM)
    assert awg._channel_config[1].voltage_unit is VoltageUnit.DBM


def test_output_enable_records_state_in_channel_config(awg: InstroAWG) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.output_enable(1, True)
    assert awg._channel_config[1].output_enabled is True
    awg.output_enable(1, False)
    assert awg._channel_config[1].output_enabled is False


def test_output_enable_before_waveform_does_not_create_channel_config(awg: InstroAWG) -> None:
    """Config presence gates start(); commands that don't require a waveform must not fake one."""
    awg.output_enable(1, True)
    awg.set_voltage_unit(1, VoltageUnit.VRMS)
    assert 1 not in awg._channel_config


def test_set_std_waveform_preserves_tracked_fields(awg: InstroAWG) -> None:
    """Instruments keep frequency, unit, and output state across a function change; the config must match."""
    awg.set_std_waveform(1, WaveformType.SINE)
    awg.set_std_frequency(1, 5000.0)
    awg.set_std_amplitude(1, 2.5, VoltageUnit.VRMS)
    awg.output_enable(1, True)
    awg.set_std_waveform(1, WaveformType.SQUARE)
    config = awg._channel_config[1]
    assert config.waveform is WaveformType.SQUARE
    assert config.frequency_hz == 5000.0
    assert config.voltage_unit is VoltageUnit.VRMS
    assert config.output_enabled is True


def test_set_std_frequency_check_errors_failure_leaves_frequency_unrecorded(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)
    mock_driver.check_errors.side_effect = RuntimeError("instrument error queue not empty")
    with pytest.raises(RuntimeError, match="instrument error queue not empty"):
        awg.set_std_frequency(1, 5000.0)
    assert awg._channel_config[1].frequency_hz is None


# ---------------------------------------------------------------------------
# Channel range validation — `awg` fixture is num_channels=2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_std_waveform", (WaveformType.SINE,)),
        ("set_std_offset", (0.5,)),
        ("output_enable", (True,)),
        ("set_std_output_load", (50.0,)),
        ("get_std_waveform", ()),
        ("get_std_amplitude", ()),
        ("get_std_frequency", ()),
        ("get_output_state", ()),
        ("get_phase", ()),
        ("get_std_offset", ()),
        ("get_std_output_load", ()),
        ("set_voltage_unit", (VoltageUnit.VRMS,)),
        ("get_voltage_unit", ()),
        ("set_high_level", (3.3,)),
        ("set_low_level", (0.0,)),
        ("set_std_frequency", (5000.0,)),
        ("set_std_amplitude", (2.5, VoltageUnit.VPP)),
        ("set_phase", (90.0,)),
        ("set_square_duty_cycle", (50.0,)),
        ("set_ramp_symmetry", (75.0,)),
        ("set_pulse_width", (0.001,)),
        ("set_pulse_delay", (0.002,)),
    ],
)
@pytest.mark.parametrize("channel", [0, 3])
def test_channel_scoped_methods_raise_for_out_of_range_channel(
    awg: InstroAWG, method_name: str, args: tuple[object, ...], channel: int
) -> None:
    with pytest.raises(ValueError, match=f"channel {channel} out of range"):
        getattr(awg, method_name)(channel, *args)


def test_channel_at_lower_bound_is_valid(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(1, WaveformType.SINE)


def test_channel_at_upper_bound_is_valid(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_waveform(2, WaveformType.SINE)


# ---------------------------------------------------------------------------
# Measurement getters
# ---------------------------------------------------------------------------


def test_get_std_waveform_returns_waveform_enum_not_measurement(awg: InstroAWG, mock_driver: MagicMock) -> None:
    result = awg.get_std_waveform(1)
    mock_driver.get_std_waveform.assert_called_once_with(channel=1)
    assert result == WaveformType.SINE


def test_get_std_amplitude_returns_tuple_not_measurement(awg: InstroAWG, mock_driver: MagicMock) -> None:
    result = awg.get_std_amplitude(1)
    mock_driver.get_std_amplitude.assert_called_once_with(channel=1)
    assert result == (2.5, VoltageUnit.VPP)


def test_get_voltage_unit_returns_enum_not_measurement(awg: InstroAWG, mock_driver: MagicMock) -> None:
    result = awg.get_voltage_unit(1)
    mock_driver.get_voltage_unit.assert_called_once_with(channel=1)
    assert result == VoltageUnit.VPP


def test_get_std_frequency_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.get_std_frequency(1)
    mock_driver.get_std_frequency.assert_called_once_with(channel=1)


def test_get_std_frequency_returns_measurement_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_std_frequency(1)
    assert meas is not None
    assert "test_awg.ch1.frequency" in meas.channel_data
    assert meas.channel_data["test_awg.ch1.frequency"] == [1000.0]


def test_get_std_offset_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.get_std_offset(1)
    mock_driver.get_std_offset.assert_called_once_with(channel=1)


def test_get_std_offset_returns_measurement_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_std_offset(1)
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


def test_get_phase_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.get_phase(1)
    mock_driver.get_phase.assert_called_once_with(channel=1)


def test_get_phase_returns_measurement_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_phase(1)
    assert meas is not None
    assert "test_awg.ch1.phase" in meas.channel_data
    assert meas.channel_data["test_awg.ch1.phase"] == [0.0]


def test_get_std_output_load_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.get_std_output_load(1)
    mock_driver.get_std_output_load.assert_called_once_with(channel=1)


def test_get_std_output_load_returns_measurement_with_correct_descriptor(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    meas = awg.get_std_output_load(1)
    assert meas is not None
    assert "test_awg.ch1.load" in meas.channel_data
    assert meas.channel_data["test_awg.ch1.load"] == [50.0]


def test_get_std_output_load_high_z_publishes_float_inf(awg: InstroAWG, mock_driver: MagicMock) -> None:
    mock_driver.get_std_output_load.return_value = None
    meas = awg.get_std_output_load(1)
    assert meas is not None
    assert meas.channel_data["test_awg.ch1.load"] == [float("inf")]
