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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_driver() -> MagicMock:
    driver = MagicMock(spec=AWGDriverBase)
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


def test_driver_property_returns_underlying_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    assert awg.driver is mock_driver


def test_check_errors_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.check_errors()
    mock_driver.check_errors.assert_called_once()


# ---------------------------------------------------------------------------
# Channel validation
# ---------------------------------------------------------------------------


def test_channel_valid_returns_int(awg: InstroAWG) -> None:
    assert awg.channel(1) == 1
    assert awg.channel(2) == 2


def test_channel_zero_raises_value_error(awg: InstroAWG) -> None:
    with pytest.raises(ValueError, match="Channel 0 is out of range"):
        awg.channel(0)


def test_channel_above_num_channels_raises_value_error(awg: InstroAWG) -> None:
    with pytest.raises(ValueError, match="Channel 3 is out of range"):
        awg.channel(3)


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
    awg.set_std_frequency(1, 5000.0)
    mock_driver.set_std_frequency.assert_called_once_with(1, 5000.0)


def test_set_std_frequency_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_frequency(1, 5000.0)
    assert "test_awg.ch1.frequency.cmd" in cmd.channel_data


def test_set_std_amplitude_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_amplitude(1, 2.5, VoltageUnit.VPP)
    mock_driver.set_std_amplitude.assert_called_once_with(channel=1, amplitude=2.5, unit=VoltageUnit.VPP)


def test_set_std_amplitude_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_amplitude(1, 2.5, VoltageUnit.VPP)
    assert "test_awg.ch1.amplitude.cmd" in cmd.channel_data


def test_set_std_offset_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_std_offset(1, 0.5)
    mock_driver.set_std_offset.assert_called_once_with(1, 0.5)


def test_set_std_offset_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_offset(1, 0.5)
    assert "test_awg.ch1.offset.cmd" in cmd.channel_data


def test_output_enable_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.output_enable(1, True)
    mock_driver.output_enable.assert_called_once_with(1, True)


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


def test_set_std_output_load_high_z_publishes_inf_string(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_output_load(1, None)
    assert cmd.channel_data["test_awg.ch1.load.cmd"] == "INF"


def test_set_phase_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_phase(1, 90.0)
    mock_driver.set_phase.assert_called_once_with(1, 90.0)


def test_set_phase_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_phase(1, 90.0)
    assert "test_awg.ch1.phase.cmd" in cmd.channel_data


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
    awg.set_high_level(1, 3.3)
    mock_driver.set_high_level.assert_called_once_with(1, 3.3)


def test_set_high_level_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_high_level(1, 3.3)
    assert "test_awg.ch1.high_level.cmd" in cmd.channel_data


def test_set_low_level_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_low_level(1, 0.0)
    mock_driver.set_low_level.assert_called_once_with(1, 0.0)


def test_set_low_level_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_low_level(1, 0.0)
    assert "test_awg.ch1.low_level.cmd" in cmd.channel_data


# ---------------------------------------------------------------------------
# Waveform-specific optional setters
# ---------------------------------------------------------------------------


def test_set_square_duty_cycle_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_square_duty_cycle(1, 50.0)
    mock_driver.set_square_duty_cycle.assert_called_once_with(1, 50.0)


def test_set_square_duty_cycle_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_square_duty_cycle(1, 50.0)
    assert "test_awg.ch1.square.duty_cycle.cmd" in cmd.channel_data


def test_set_ramp_symmetry_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_ramp_symmetry(1, 75.0)
    mock_driver.set_ramp_symmetry.assert_called_once_with(1, 75.0)


def test_set_ramp_symmetry_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_ramp_symmetry(1, 75.0)
    assert "test_awg.ch1.ramp.symmetry.cmd" in cmd.channel_data


def test_set_pulse_width_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.set_pulse_width(1, 0.001)
    mock_driver.set_pulse_width.assert_called_once_with(1, 0.001)


def test_set_pulse_width_returns_command_with_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_pulse_width(1, 0.001)
    assert "test_awg.ch1.pulse.width.cmd" in cmd.channel_data


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


# ---------------------------------------------------------------------------
# configure_std_channel convenience method
# ---------------------------------------------------------------------------


def test_configure_std_channel_returns_four_commands_by_default(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmds = awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP)
    assert len(cmds) == 4


def test_configure_std_channel_calls_all_four_setters(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP)
    mock_driver.set_std_waveform.assert_called_once_with(channel=1, waveform=WaveformType.SINE)
    mock_driver.set_std_frequency.assert_called_once_with(1, 1000.0)
    mock_driver.set_std_amplitude.assert_called_once_with(channel=1, amplitude=2.5, unit=VoltageUnit.VPP)
    mock_driver.set_std_offset.assert_called_once_with(1, 0.0)


def test_configure_std_channel_default_offset_is_zero(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP)
    mock_driver.set_std_offset.assert_called_once_with(1, 0.0)


def test_configure_std_channel_custom_offset(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP, offset_v=1.0)
    mock_driver.set_std_offset.assert_called_once_with(1, 1.0)


def test_configure_std_channel_with_load_returns_five_commands(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmds = awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP, load=50.0)
    assert len(cmds) == 5
    mock_driver.set_std_output_load.assert_called_once_with(channel=1, load=50.0)


def test_configure_std_channel_with_enable_returns_five_commands(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmds = awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP, enable=True)
    assert len(cmds) == 5
    mock_driver.output_enable.assert_called_once_with(1, True)


def test_configure_std_channel_with_load_and_enable_returns_six_commands(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    cmds = awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP, load=50.0, enable=True)
    assert len(cmds) == 6


def test_configure_std_channel_omit_load_does_not_call_set_load(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Omitting load (using _UNSET sentinel) must NOT call set_std_output_load."""
    awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP)
    mock_driver.set_std_output_load.assert_not_called()


def test_configure_std_channel_load_none_calls_set_load_with_none(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Explicitly passing load=None (high-Z) must call set_std_output_load with None."""
    awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP, load=None)
    mock_driver.set_std_output_load.assert_called_once_with(channel=1, load=None)


def test_configure_std_channel_enable_none_does_not_call_output_enable(awg: InstroAWG, mock_driver: MagicMock) -> None:
    """Omitting enable (default None) must NOT call output_enable."""
    awg.configure_std_channel(1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP)
    mock_driver.output_enable.assert_not_called()
