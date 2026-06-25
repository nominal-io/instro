"""Tests for AWGDriverBase contract and InstroAWG composition."""

from unittest.mock import MagicMock

import pytest

from instro.awg.awg import AWGDriverBase, InstroAWG
from instro.awg.types import Channel, VoltageUnit, WaveformType


# --- Minimal concrete driver for base class contract tests ---


class _MinimalAWGDriver(AWGDriverBase):
    """Implements all abstract methods with no-ops to satisfy the contract."""

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def check_errors(self) -> None:
        pass

    def set_std_waveform(self, channel: Channel, waveform: WaveformType) -> None:
        pass

    def get_std_waveform(self, channel: Channel) -> WaveformType:
        return WaveformType.SINE

    def set_std_frequency(self, channel: Channel, frequency: float) -> None:
        pass

    def get_std_frequency(self, channel: Channel) -> float:
        return 1000.0

    def set_std_amplitude(self, channel: Channel, amplitude: float, unit: VoltageUnit) -> None:
        pass

    def set_std_offset(self, channel: Channel, offset: float) -> None:
        pass

    def get_std_offset(self, channel: Channel) -> float:
        return 0.0

    def output_enable(self, channel: Channel, enable: bool) -> None:
        pass

    def get_output_state(self, channel: Channel) -> bool:
        return False

    def set_std_output_load(self, channel: Channel, load: float | None) -> None:
        pass

    def get_std_output_load(self, channel: Channel) -> float | None:
        return None


# --- AWGDriverBase contract tests ---


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


# --- InstroAWG fixtures ---


@pytest.fixture
def mock_driver() -> MagicMock:
    driver = MagicMock(spec=AWGDriverBase)
    driver.get_std_waveform.return_value = WaveformType.SINE
    driver.get_std_frequency.return_value = 1000.0
    driver.get_output_state.return_value = False
    driver.get_std_output_load.return_value = 50.0
    return driver


@pytest.fixture
def awg(mock_driver: MagicMock) -> InstroAWG:
    return InstroAWG(name="test_awg", driver=mock_driver, num_channels=2)


# --- InstroAWG lifecycle ---


def test_open_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.open()
    mock_driver.open.assert_called_once()


def test_close_delegates_to_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.open()
    awg.close()
    mock_driver.close.assert_called_once()


def test_driver_property_returns_underlying_driver(awg: InstroAWG, mock_driver: MagicMock) -> None:
    assert awg.driver is mock_driver


# --- Commands ---


def test_set_std_waveform_delegates_and_returns_command(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_waveform(Channel.CH1, WaveformType.SINE)
    mock_driver.set_std_waveform.assert_called_once_with(channel=Channel.CH1, waveform=WaveformType.SINE)
    assert "test_awg.ch1.waveform.cmd" in cmd.channel_data


def test_set_std_waveform_ch2_uses_correct_descriptor(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_waveform(Channel.CH2, WaveformType.SQUARE)
    assert "test_awg.ch2.waveform.cmd" in cmd.channel_data


def test_set_std_frequency_delegates_and_returns_command(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_frequency(Channel.CH1, 5000.0)
    mock_driver.set_std_frequency.assert_called_once_with(channel=Channel.CH1, frequency=5000.0)
    assert "test_awg.ch1.frequency.cmd" in cmd.channel_data


def test_set_std_amplitude_delegates_and_returns_command(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_amplitude(Channel.CH1, 2.5, VoltageUnit.VPP)
    mock_driver.set_std_amplitude.assert_called_once_with(channel=Channel.CH1, amplitude=2.5, unit=VoltageUnit.VPP)
    assert "test_awg.ch1.amplitude.cmd" in cmd.channel_data


def test_set_std_offset_delegates_and_returns_command(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_offset(Channel.CH1, 0.5)
    mock_driver.set_std_offset.assert_called_once_with(channel=Channel.CH1, offset=0.5)
    assert "test_awg.ch1.offset.cmd" in cmd.channel_data


def test_output_enable_delegates_and_returns_command(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.output_enable(Channel.CH1, True)
    mock_driver.output_enable.assert_called_once_with(channel=Channel.CH1, enable=True)
    assert "test_awg.ch1.enabled.cmd" in cmd.channel_data


def test_set_std_output_load_delegates_and_returns_command(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_output_load(Channel.CH1, 50.0)
    mock_driver.set_std_output_load.assert_called_once_with(channel=Channel.CH1, load=50.0)
    assert "test_awg.ch1.load.cmd" in cmd.channel_data


def test_set_std_output_load_high_z_publishes_inf(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmd = awg.set_std_output_load(Channel.CH1, None)
    mock_driver.set_std_output_load.assert_called_once_with(channel=Channel.CH1, load=None)
    assert cmd.channel_data["test_awg.ch1.load.cmd"] == "INF"


# --- Measurements ---


def test_get_std_waveform_delegates_and_returns_enum(awg: InstroAWG, mock_driver: MagicMock) -> None:
    # get_std_waveform returns the enum directly — not published as Measurement (non-numeric)
    result = awg.get_std_waveform(Channel.CH1)
    mock_driver.get_std_waveform.assert_called_once_with(channel=Channel.CH1)
    assert result == WaveformType.SINE


def test_get_std_frequency_delegates_and_returns_measurement(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_std_frequency(Channel.CH1)
    mock_driver.get_std_frequency.assert_called_once_with(channel=Channel.CH1)
    assert "test_awg.ch1.frequency" in meas.channel_data


def test_get_output_state_delegates_and_returns_measurement(awg: InstroAWG, mock_driver: MagicMock) -> None:
    meas = awg.get_output_state(Channel.CH1)
    mock_driver.get_output_state.assert_called_once_with(channel=Channel.CH1)
    assert "test_awg.ch1.enabled" in meas.channel_data


# --- configure_std_channel convenience method ---


def test_configure_std_channel_calls_all_four_setters(awg: InstroAWG, mock_driver: MagicMock) -> None:
    cmds = awg.configure_std_channel(Channel.CH1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP)
    assert len(cmds) == 4
    mock_driver.set_std_waveform.assert_called_once_with(channel=Channel.CH1, waveform=WaveformType.SINE)
    mock_driver.set_std_frequency.assert_called_once_with(channel=Channel.CH1, frequency=1000.0)
    mock_driver.set_std_amplitude.assert_called_once_with(channel=Channel.CH1, amplitude=2.5, unit=VoltageUnit.VPP)
    mock_driver.set_std_offset.assert_called_once_with(channel=Channel.CH1, offset=0.0)


def test_configure_std_channel_default_offset_is_zero(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.configure_std_channel(Channel.CH1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP)
    mock_driver.set_std_offset.assert_called_once_with(channel=Channel.CH1, offset=0.0)


def test_configure_std_channel_custom_offset(awg: InstroAWG, mock_driver: MagicMock) -> None:
    awg.configure_std_channel(Channel.CH1, WaveformType.SINE, 1000.0, 2.5, VoltageUnit.VPP, offset_v=1.0)
    mock_driver.set_std_offset.assert_called_once_with(channel=Channel.CH1, offset=1.0)
