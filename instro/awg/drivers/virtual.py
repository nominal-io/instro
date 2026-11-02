"""Virtual AWG driver: settings held in-process, with no transport, server, or hardware."""

from __future__ import annotations

from dataclasses import dataclass

from instro.awg.awg import AWGDriverBase
from instro.awg.types import (
    AmplitudeMeasurementUnit,
    BurstTriggerSource,
    BurstType,
    GatePolarity,
    ModulationType,
    Sine,
    SweepTriggerSource,
    SweepType,
    Waveform,
)


@dataclass
class _ChannelState:
    """One channel's settings; defaults follow a Keysight 33500-series reset."""

    waveform: Waveform = Sine(frequency_hz=1000.0)
    amplitude: float = 0.1
    amplitude_unit: AmplitudeMeasurementUnit = AmplitudeMeasurementUnit.VPP
    offset: float = 0.0
    output_enabled: bool = False
    load: float | None = 50.0
    modulation_type: ModulationType = ModulationType.AM
    modulation_enabled: bool = False
    burst_type: BurstType = BurstType.NCYCLE
    burst_enabled: bool = False
    burst_trigger: BurstTriggerSource = BurstTriggerSource.INTERNAL
    burst_delay: float = 0.0
    burst_gate_polarity: GatePolarity = GatePolarity.NORM
    burst_ncycles: int = 1
    burst_period: float = 0.01
    sweep_type: SweepType = SweepType.LINEAR
    sweep_enabled: bool = False
    sweep_trigger: SweepTriggerSource = SweepTriggerSource.INTERNAL
    sweep_start_freq: float = 100.0
    sweep_end_freq: float = 1000.0
    sweep_time: float = 1.0
    sweep_start_hold_time: float = 0.0
    sweep_stop_hold_time: float = 0.0
    sweep_return_time: float = 0.0


class VirtualAWG(AWGDriverBase):
    """AWG simulated in-process: no transport, server, or hardware.

    Each channel holds every setting `InstroAWG` can make and reads it back, so any
    `InstroAWG` call works and set-then-get round-trips. Nothing is generated: the virtual
    AWG keeps state only. Channels start as a real AWG resets: a 1 kHz sine at 100 mVpp,
    0 V offset, 50 ohm load, output off, and modulation, burst, and sweep disabled.
    """

    def __init__(self, num_channels: int = 2) -> None:
        """Create a virtual AWG.

        Args:
            num_channels (int): Number of output channels, numbered from 1. Pass the same
                count to `InstroAWG`.

        Raises:
            ValueError: ``num_channels`` is less than 1.

        Example::

            from instro.awg import AmplitudeMeasurementUnit, InstroAWG, Sine
            from instro.awg.drivers import VirtualAWG

            awg = InstroAWG(name="awg", driver=VirtualAWG(num_channels=2), num_channels=2)
            with awg:
                awg.set_waveform(1, Sine(frequency_hz=5000.0))
                awg.set_amplitude(1, 2.0, AmplitudeMeasurementUnit.VPP)
                awg.output_enable(1, True)
                print(awg.get_waveform(1))  # Sine(frequency_hz=5000.0, phase_deg=0.0)
                print(awg.get_output_state(1).latest)  # True
        """
        if num_channels < 1:
            raise ValueError(f"num_channels must be at least 1, got {num_channels}")
        self._channels = {channel: _ChannelState() for channel in range(1, num_channels + 1)}

    def open(self) -> None:
        """Nothing to connect to."""

    def close(self) -> None:
        """Nothing to disconnect."""

    def set_waveform(self, channel: int, waveform: Waveform) -> None:
        self._state(channel).waveform = waveform

    def get_waveform(self, channel: int) -> Waveform:
        return self._state(channel).waveform

    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
        state = self._state(channel)
        state.amplitude = amplitude
        state.amplitude_unit = unit

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        state = self._state(channel)
        return (state.amplitude, state.amplitude_unit)

    def set_offset(self, channel: int, offset: float) -> None:
        self._state(channel).offset = offset

    def get_offset(self, channel: int) -> float:
        return self._state(channel).offset

    def output_enable(self, channel: int, enable: bool) -> None:
        self._state(channel).output_enabled = enable

    def get_output_state(self, channel: int) -> bool:
        return self._state(channel).output_enabled

    def set_output_load(self, channel: int, load: float | None) -> None:
        self._state(channel).load = load

    def get_output_load(self, channel: int) -> float | None:
        return self._state(channel).load

    def align_phase(self) -> None:
        """Accepted; there is no generated signal to align."""

    def set_modulation(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float) -> None:
        """Record the modulation type; ``shape`` and ``magnitude`` are accepted and not read back."""
        self._state(channel).modulation_type = mod_type

    def modulation_enable(self, channel: int, enable: bool) -> None:
        self._state(channel).modulation_enabled = enable

    def get_modulation_type(self, channel: int) -> ModulationType:
        return self._state(channel).modulation_type

    def get_modulation_state(self, channel: int) -> bool:
        return self._state(channel).modulation_enabled

    def set_burst(self, channel: int, burst_type: BurstType) -> None:
        self._state(channel).burst_type = burst_type

    def burst_enable(self, channel: int, enable: bool) -> None:
        self._state(channel).burst_enabled = enable

    def get_burst_type(self, channel: int) -> BurstType:
        return self._state(channel).burst_type

    def get_burst_state(self, channel: int) -> bool:
        return self._state(channel).burst_enabled

    def set_burst_trigger(self, channel: int, source: BurstTriggerSource) -> None:
        self._state(channel).burst_trigger = source

    def get_burst_trigger(self, channel: int) -> BurstTriggerSource:
        return self._state(channel).burst_trigger

    def fire_burst_trigger(self, channel: int) -> None:
        """Check that a burst could fire, as a real AWG does; nothing is generated.

        Raises:
            ValueError: burst mode is disabled, or the burst trigger source is not MANUAL.
        """
        state = self._state(channel)
        if not state.burst_enabled:
            raise ValueError(
                f"Cannot fire a burst trigger on channel {channel} unless burst mode is already"
                " enabled, call burst_enable(channel, True) first"
            )
        if state.burst_trigger is not BurstTriggerSource.MANUAL:
            raise ValueError(
                f"Cannot fire a burst trigger on channel {channel} unless the trigger source is"
                f" already MANUAL, call set_burst_trigger(channel, BurstTriggerSource.MANUAL) first."
                f" Current source: {state.burst_trigger.name}"
            )

    def set_burst_delay(self, channel: int, delay_s: float) -> None:
        self._state(channel).burst_delay = delay_s

    def get_burst_delay(self, channel: int) -> float:
        return self._state(channel).burst_delay

    def set_burst_gate_polarity(self, channel: int, gate_polarity: GatePolarity) -> None:
        self._state(channel).burst_gate_polarity = gate_polarity

    def get_burst_gate_polarity(self, channel: int) -> GatePolarity:
        return self._state(channel).burst_gate_polarity

    def set_burst_ncycles(self, channel: int, n_cycles: int) -> None:
        self._state(channel).burst_ncycles = n_cycles

    def get_burst_ncycles(self, channel: int) -> int:
        return self._state(channel).burst_ncycles

    def set_burst_period(self, channel: int, period: float) -> None:
        self._state(channel).burst_period = period

    def get_burst_period(self, channel: int) -> float:
        return self._state(channel).burst_period

    def set_sweep(self, channel: int, sweep_type: SweepType) -> None:
        self._state(channel).sweep_type = sweep_type

    def get_sweep_type(self, channel: int) -> SweepType:
        return self._state(channel).sweep_type

    def sweep_enable(self, channel: int, enable: bool) -> None:
        self._state(channel).sweep_enabled = enable

    def get_sweep_state(self, channel: int) -> bool:
        return self._state(channel).sweep_enabled

    def set_sweep_trigger(self, channel: int, source: SweepTriggerSource) -> None:
        self._state(channel).sweep_trigger = source

    def get_sweep_trigger(self, channel: int) -> SweepTriggerSource:
        return self._state(channel).sweep_trigger

    def set_sweep_start_freq(self, channel: int, frequency_hz: float) -> None:
        self._state(channel).sweep_start_freq = frequency_hz

    def get_sweep_start_freq(self, channel: int) -> float:
        return self._state(channel).sweep_start_freq

    def set_sweep_end_freq(self, channel: int, frequency_hz: float) -> None:
        self._state(channel).sweep_end_freq = frequency_hz

    def get_sweep_end_freq(self, channel: int) -> float:
        return self._state(channel).sweep_end_freq

    def set_sweep_time(self, channel: int, sweep_time: float) -> None:
        self._state(channel).sweep_time = sweep_time

    def get_sweep_time(self, channel: int) -> float:
        return self._state(channel).sweep_time

    def set_sweep_start_hold_time(self, channel: int, hold_time: float) -> None:
        self._state(channel).sweep_start_hold_time = hold_time

    def get_sweep_start_hold_time(self, channel: int) -> float:
        return self._state(channel).sweep_start_hold_time

    def set_sweep_stop_hold_time(self, channel: int, hold_time: float) -> None:
        self._state(channel).sweep_stop_hold_time = hold_time

    def get_sweep_stop_hold_time(self, channel: int) -> float:
        return self._state(channel).sweep_stop_hold_time

    def set_sweep_return_time(self, channel: int, return_time: float) -> None:
        self._state(channel).sweep_return_time = return_time

    def get_sweep_return_time(self, channel: int) -> float:
        return self._state(channel).sweep_return_time

    def fire_sweep_trigger(self, channel: int) -> None:
        """Check that a sweep could fire, as a real AWG does; nothing is generated.

        Raises:
            ValueError: sweep mode is disabled, or the sweep trigger source is not MANUAL.
        """
        state = self._state(channel)
        if not state.sweep_enabled:
            raise ValueError(
                f"Cannot fire a sweep trigger on channel {channel} unless sweep mode is already"
                " enabled, call sweep_enable(channel, True) first"
            )
        if state.sweep_trigger is not SweepTriggerSource.MANUAL:
            raise ValueError(
                f"Cannot fire a sweep trigger on channel {channel} unless the trigger source is"
                f" already MANUAL, call set_sweep_trigger(channel, SweepTriggerSource.MANUAL) first."
                f" Current source: {state.sweep_trigger.name}"
            )

    def _state(self, channel: int) -> _ChannelState:
        if channel not in self._channels:
            raise ValueError(f"VirtualAWG channel must be 1-{len(self._channels)}, got {channel}")
        return self._channels[channel]
