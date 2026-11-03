"""Tests for the transport-free VirtualScope driver."""

from __future__ import annotations

import pytest

from instro.scope import (
    AcquisitionMode,
    AcquisitionState,
    ChannelState,
    Coupling,
    InstroScope,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerStatus,
    TriggerType,
)
from instro.scope.drivers import VirtualScope
from instro.scope.drivers.virtual import SineWave, SquareWave


def test_waveform_follows_signal_timebase_and_trigger() -> None:
    driver = VirtualScope(num_channels=2, signals={2: SineWave(frequency=5e3, amplitude=0.5, offset=1.0)})
    with InstroScope(name="scope", driver=driver, num_channels=2) as scope:
        scope.set_horizontal_scale(100e-6)
        scope.set_trigger_source(2)
        scope.set_trigger_slope(TriggerSlope.FALLING)
        scope.set_trigger_level(1.25)
        scope.single()

        waveform = scope.fetch_waveform(channel=2)
        t_acquisition = int(waveform.tags["t_acquisition"])
        times = [t - t_acquisition for t in waveform.timestamps]
        voltages = waveform.channel_data["scope.ch2.waveform"]
        trigger_index = times.index(0)
        assert len(times) == 1000
        assert (times[0], times[-1] + times[1] - times[0]) == (-500_000, 500_000)
        assert voltages[trigger_index] == pytest.approx(1.25, abs=0.05)
        assert voltages[trigger_index + 10] < voltages[trigger_index] < voltages[trigger_index - 10]

        assert scope.measure(ScopeMeasurementType.VPP, channel=2).latest == pytest.approx(1.0, rel=0.1)
        assert scope.measure(ScopeMeasurementType.VAVG, channel=2).latest == pytest.approx(1.0, abs=0.02)
        assert scope.measure(ScopeMeasurementType.FREQUENCY, channel=2).latest == 5e3
        assert scope.measure(ScopeMeasurementType.VRMS, channel=1).latest == pytest.approx(2**-0.5, rel=0.02)
        assert scope.get_sample_rate().latest == 1e6

        scope.set_coupling(Coupling.AC, channel=2)
        scope.single()
        assert scope.measure(ScopeMeasurementType.VAVG, channel=2).latest == pytest.approx(0.0, abs=0.02)


def test_defaults_put_a_sine_on_channel_1_and_a_square_on_channel_2() -> None:
    with InstroScope(name="scope", driver=VirtualScope(), num_channels=4) as scope:
        scope.set_trigger_source(2)
        scope.set_trigger_slope(TriggerSlope.FALLING)
        scope.single()

        assert scope.measure(ScopeMeasurementType.VRMS, channel=1).latest == pytest.approx(2**-0.5, rel=0.02)
        assert scope.measure(ScopeMeasurementType.VRMS, channel=2).latest == pytest.approx(1.0, rel=0.02)
        assert scope.measure(ScopeMeasurementType.VPP, channel=2).latest == pytest.approx(2.0, rel=0.05)
        assert scope.measure(ScopeMeasurementType.FREQUENCY, channel=2).latest == 1e3

        square = scope.fetch_waveform(channel=2).channel_data["scope.ch2.waveform"]
        assert square[495] == pytest.approx(1.0, abs=0.05)
        assert square[505] == pytest.approx(-1.0, abs=0.05)


def test_stopped_scope_holds_its_capture_and_running_scope_refreshes() -> None:
    driver = VirtualScope(num_channels=1)
    with InstroScope(name="scope", driver=driver, num_channels=1) as scope:
        assert scope.get_acquisition_state().latest == AcquisitionState.RUNNING.value
        running = [scope.fetch_waveform(channel=1).channel_data["scope.ch1.waveform"] for _ in range(2)]
        assert running[0] != running[1]

        scope.stop_acquisition()
        stopped = scope.fetch_waveform(channel=1).channel_data["scope.ch1.waveform"]
        driver.set_signal(SineWave(amplitude=0.1), channel=1)
        scope.set_vertical_scale(0.05, channel=1)
        assert scope.fetch_waveform(channel=1).channel_data["scope.ch1.waveform"] == stopped
        assert scope.measure(ScopeMeasurementType.VPP, channel=1).latest == pytest.approx(2.0, rel=0.05)
        assert scope.get_acquisition_state().latest == AcquisitionState.STOPPED.value

        scope.single()
        assert scope.measure(ScopeMeasurementType.VPP, channel=1).latest == pytest.approx(0.2, rel=0.1)


def test_rejects_invalid_signals_settings_and_channels() -> None:
    with pytest.raises(ValueError, match="frequency must be finite and not negative"):
        SineWave(frequency=-1.0)
    with pytest.raises(ValueError, match="amplitude must be finite and not negative"):
        SquareWave(amplitude=float("nan"))
    with pytest.raises(ValueError, match="channels 1 to 2, got 3"):
        VirtualScope(num_channels=2, signals={3: SineWave()})

    driver = VirtualScope(num_channels=2)
    with pytest.raises(ValueError, match="probe attenuation must be positive"):
        driver.set_probe_attenuation(0.0, channel=1)
    with pytest.raises(ValueError, match="channels 1 to 2, got 3"):
        driver.fetch_waveform(channel=3)


def test_every_setting_reads_back_and_file_operations_are_unsupported() -> None:
    configured = ChannelState(vertical_scale=0.2, vertical_offset=-0.5, coupling=Coupling.AC, probe_attenuation=10.0)
    with InstroScope(name="scope", driver=VirtualScope(), num_channels=2) as scope:
        scope.set_vertical_scale(0.2, channel=2)
        scope.set_vertical_offset(-0.5, channel=2)
        scope.set_coupling(Coupling.AC, channel=2)
        scope.set_probe_attenuation(10.0, channel=2)
        scope.set_horizontal_scale(1e-3)
        scope.set_average_count(64)
        scope.set_acquisition_mode(AcquisitionMode.AVERAGE)
        scope.set_trigger_type(TriggerType.PULSE)
        scope.set_trigger_mode(TriggerMode.NORMAL)
        scope.run()
        scope.force_trigger()
        assert scope.get_trigger_status().latest == TriggerStatus.TRIGGERED.value

        state = scope.sync_configuration()
        assert state.channels[2] == configured
        assert (state.horizontal_scale, state.acquisition_mode, state.average_count) == (
            1e-3,
            AcquisitionMode.AVERAGE,
            64,
        )

        with pytest.raises(NotImplementedError):
            scope.save_screenshot("screen.png")
        with pytest.raises(NotImplementedError):
            scope.save_settings("setup", to_instrument=True)
        with pytest.raises(NotImplementedError):
            scope.load_settings("setup", from_instrument=True)
