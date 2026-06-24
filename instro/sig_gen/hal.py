"""Signal generator instrument driver contract."""

from __future__ import annotations

import abc

from instro.sig_gen.types import (
    BurstMode,
    Channel,
    ClockSource,
    ModSource,
    ModWaveform,
    OutputPolarity,
    SweepSpacing,
    TriggerSlope,
    TriggerSource,
    VoltageUnit,
    WaveformType,
)


class SigGenDriverBase(abc.ABC):
    """Base class for signal generator drivers."""

    @classmethod
    @abc.abstractmethod
    def match_idn(cls, idn: str) -> bool:
        """Return True if this driver handles the instrument identified by ``idn``."""

    @abc.abstractmethod
    def open(self) -> None:
        """Open the driver's underlying transport."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the driver's underlying transport."""

    # --- Output ---

    @abc.abstractmethod
    def set_output_enable(self, channel: Channel, enable: bool) -> None:
        """Enable or disable the output on ``channel``."""

    @abc.abstractmethod
    def set_output_load(self, channel: Channel, ohms: float | None) -> None:
        """Set the output load impedance; ``None`` means high-Z."""

    @abc.abstractmethod
    def set_output_polarity(self, channel: Channel, polarity: OutputPolarity) -> None:
        """Set the output polarity on ``channel``."""

    def set_sync_output_enable(self, enable: bool) -> None:
        """Enable or disable the CH1 sync output (rear panel)."""
        raise NotImplementedError(f"set_sync_output_enable is not implemented for {type(self).__name__}")

    # --- APPLy shortcut ---

    @abc.abstractmethod
    def apply_waveform(
        self,
        channel: Channel,
        waveform: WaveformType,
        frequency: float,
        amplitude: float,
        offset: float,
    ) -> None:
        """Configure ``channel`` with the APPLy compound command."""

    # --- Discrete waveform params ---

    @abc.abstractmethod
    def set_function(self, channel: Channel, waveform: WaveformType) -> None:
        """Set the waveform function on ``channel``."""

    @abc.abstractmethod
    def set_frequency(self, channel: Channel, frequency_hz: float) -> None:
        """Set the output frequency (Hz) on ``channel``."""

    @abc.abstractmethod
    def set_amplitude(self, channel: Channel, amplitude: float) -> None:
        """Set the output amplitude on ``channel``."""

    def set_voltage_unit(self, channel: Channel, unit: VoltageUnit) -> None:
        """Set the voltage unit for amplitude on ``channel``."""
        raise NotImplementedError(f"set_voltage_unit is not implemented for {type(self).__name__}")

    @abc.abstractmethod
    def set_offset(self, channel: Channel, offset_v: float) -> None:
        """Set the DC offset (volts) on ``channel``."""

    def set_high_level(self, channel: Channel, volts: float) -> None:
        """Set the high voltage level on ``channel``."""
        raise NotImplementedError(f"set_high_level is not implemented for {type(self).__name__}")

    def set_low_level(self, channel: Channel, volts: float) -> None:
        """Set the low voltage level on ``channel``."""
        raise NotImplementedError(f"set_low_level is not implemented for {type(self).__name__}")

    @abc.abstractmethod
    def set_phase(self, channel: Channel, phase_deg: float) -> None:
        """Set the output phase (degrees) on ``channel``."""

    def align_phase(self) -> None:
        """Synchronize the phase of both channels (PHAS:ALIGN)."""
        raise NotImplementedError(f"align_phase is not implemented for {type(self).__name__}")

    # --- Waveform-specific ---

    def set_square_duty_cycle(self, channel: Channel, duty_pct: float) -> None:
        """Set the square wave duty cycle (%) on ``channel``."""
        raise NotImplementedError(f"set_square_duty_cycle is not implemented for {type(self).__name__}")

    def set_ramp_symmetry(self, channel: Channel, symmetry_pct: float) -> None:
        """Set the ramp symmetry (%) on ``channel``."""
        raise NotImplementedError(f"set_ramp_symmetry is not implemented for {type(self).__name__}")

    def set_pulse_period(self, channel: Channel, period_s: float) -> None:
        """Set the pulse period (seconds) on ``channel``."""
        raise NotImplementedError(f"set_pulse_period is not implemented for {type(self).__name__}")

    def set_pulse_width(self, channel: Channel, width_s: float) -> None:
        """Set the pulse width (seconds) on ``channel``."""
        raise NotImplementedError(f"set_pulse_width is not implemented for {type(self).__name__}")

    def set_pulse_duty_cycle(self, channel: Channel, duty_pct: float) -> None:
        """Set the pulse duty cycle (%) on ``channel``."""
        raise NotImplementedError(f"set_pulse_duty_cycle is not implemented for {type(self).__name__}")

    # --- Modulation (CH1 only) ---

    def set_am_state(self, enable: bool) -> None:
        """Enable or disable AM modulation on CH1."""
        raise NotImplementedError(f"set_am_state is not implemented for {type(self).__name__}")

    def set_am_source(self, source: ModSource) -> None:
        """Set the AM modulation source."""
        raise NotImplementedError(f"set_am_source is not implemented for {type(self).__name__}")

    def set_am_internal_function(self, waveform: ModWaveform) -> None:
        """Set the AM internal modulation waveform."""
        raise NotImplementedError(f"set_am_internal_function is not implemented for {type(self).__name__}")

    def set_am_internal_frequency(self, frequency_hz: float) -> None:
        """Set the AM internal modulation frequency (Hz)."""
        raise NotImplementedError(f"set_am_internal_frequency is not implemented for {type(self).__name__}")

    def set_am_depth(self, depth_pct: float) -> None:
        """Set the AM modulation depth (%)."""
        raise NotImplementedError(f"set_am_depth is not implemented for {type(self).__name__}")

    def set_fm_state(self, enable: bool) -> None:
        """Enable or disable FM modulation on CH1."""
        raise NotImplementedError(f"set_fm_state is not implemented for {type(self).__name__}")

    def set_fm_source(self, source: ModSource) -> None:
        """Set the FM modulation source."""
        raise NotImplementedError(f"set_fm_source is not implemented for {type(self).__name__}")

    def set_fm_internal_function(self, waveform: ModWaveform) -> None:
        """Set the FM internal modulation waveform."""
        raise NotImplementedError(f"set_fm_internal_function is not implemented for {type(self).__name__}")

    def set_fm_internal_frequency(self, frequency_hz: float) -> None:
        """Set the FM internal modulation frequency (Hz)."""
        raise NotImplementedError(f"set_fm_internal_frequency is not implemented for {type(self).__name__}")

    def set_fm_deviation(self, deviation_hz: float) -> None:
        """Set the FM frequency deviation (Hz)."""
        raise NotImplementedError(f"set_fm_deviation is not implemented for {type(self).__name__}")

    def set_pm_state(self, enable: bool) -> None:
        """Enable or disable PM modulation on CH1."""
        raise NotImplementedError(f"set_pm_state is not implemented for {type(self).__name__}")

    def set_pm_source(self, source: ModSource) -> None:
        """Set the PM modulation source."""
        raise NotImplementedError(f"set_pm_source is not implemented for {type(self).__name__}")

    def set_pm_internal_function(self, waveform: ModWaveform) -> None:
        """Set the PM internal modulation waveform."""
        raise NotImplementedError(f"set_pm_internal_function is not implemented for {type(self).__name__}")

    def set_pm_internal_frequency(self, frequency_hz: float) -> None:
        """Set the PM internal modulation frequency (Hz)."""
        raise NotImplementedError(f"set_pm_internal_frequency is not implemented for {type(self).__name__}")

    def set_pm_deviation(self, deviation_deg: float) -> None:
        """Set the PM phase deviation (degrees)."""
        raise NotImplementedError(f"set_pm_deviation is not implemented for {type(self).__name__}")

    def set_fsk_state(self, enable: bool) -> None:
        """Enable or disable FSK modulation on CH1."""
        raise NotImplementedError(f"set_fsk_state is not implemented for {type(self).__name__}")

    def set_fsk_source(self, source: ModSource) -> None:
        """Set the FSK modulation source."""
        raise NotImplementedError(f"set_fsk_source is not implemented for {type(self).__name__}")

    def set_fsk_hop_frequency(self, frequency_hz: float) -> None:
        """Set the FSK hop frequency (Hz)."""
        raise NotImplementedError(f"set_fsk_hop_frequency is not implemented for {type(self).__name__}")

    def set_fsk_rate(self, rate_hz: float) -> None:
        """Set the FSK shift rate (Hz)."""
        raise NotImplementedError(f"set_fsk_rate is not implemented for {type(self).__name__}")

    # --- Sweep (CH1 only) ---

    def set_sweep_state(self, enable: bool) -> None:
        """Enable or disable frequency sweep on CH1."""
        raise NotImplementedError(f"set_sweep_state is not implemented for {type(self).__name__}")

    def set_sweep_spacing(self, spacing: SweepSpacing) -> None:
        """Set the sweep frequency spacing (linear or logarithmic)."""
        raise NotImplementedError(f"set_sweep_spacing is not implemented for {type(self).__name__}")

    def set_sweep_time(self, time_s: float) -> None:
        """Set the sweep time (seconds)."""
        raise NotImplementedError(f"set_sweep_time is not implemented for {type(self).__name__}")

    def set_sweep_start_freq(self, frequency_hz: float) -> None:
        """Set the sweep start frequency (Hz)."""
        raise NotImplementedError(f"set_sweep_start_freq is not implemented for {type(self).__name__}")

    def set_sweep_stop_freq(self, frequency_hz: float) -> None:
        """Set the sweep stop frequency (Hz)."""
        raise NotImplementedError(f"set_sweep_stop_freq is not implemented for {type(self).__name__}")

    def set_sweep_center_freq(self, frequency_hz: float) -> None:
        """Set the sweep center frequency (Hz)."""
        raise NotImplementedError(f"set_sweep_center_freq is not implemented for {type(self).__name__}")

    def set_sweep_span_freq(self, span_hz: float) -> None:
        """Set the sweep frequency span (Hz)."""
        raise NotImplementedError(f"set_sweep_span_freq is not implemented for {type(self).__name__}")

    # --- Trigger ---

    def set_trigger_source(self, source: TriggerSource) -> None:
        """Set the trigger source."""
        raise NotImplementedError(f"set_trigger_source is not implemented for {type(self).__name__}")

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        """Set the trigger input slope."""
        raise NotImplementedError(f"set_trigger_slope is not implemented for {type(self).__name__}")

    def set_trigger_delay(self, delay_s: float) -> None:
        """Set the trigger delay (seconds)."""
        raise NotImplementedError(f"set_trigger_delay is not implemented for {type(self).__name__}")

    def set_trigger_output_enable(self, enable: bool) -> None:
        """Enable or disable trigger output."""
        raise NotImplementedError(f"set_trigger_output_enable is not implemented for {type(self).__name__}")

    def set_trigger_output_slope(self, slope: TriggerSlope) -> None:
        """Set the trigger output slope."""
        raise NotImplementedError(f"set_trigger_output_slope is not implemented for {type(self).__name__}")

    # --- Burst (CH1 only) ---

    def set_burst_state(self, enable: bool) -> None:
        """Enable or disable burst mode on CH1."""
        raise NotImplementedError(f"set_burst_state is not implemented for {type(self).__name__}")

    def set_burst_mode(self, mode: BurstMode) -> None:
        """Set the burst mode (triggered or gated)."""
        raise NotImplementedError(f"set_burst_mode is not implemented for {type(self).__name__}")

    def set_burst_cycles(self, cycles: int | float) -> None:
        """Set the burst cycle count; pass ``float('inf')`` for infinite."""
        raise NotImplementedError(f"set_burst_cycles is not implemented for {type(self).__name__}")

    def set_burst_period(self, period_s: float) -> None:
        """Set the burst period (seconds)."""
        raise NotImplementedError(f"set_burst_period is not implemented for {type(self).__name__}")

    def set_burst_phase(self, phase_deg: float) -> None:
        """Set the burst start phase (degrees)."""
        raise NotImplementedError(f"set_burst_phase is not implemented for {type(self).__name__}")

    def set_burst_gate_polarity(self, polarity: OutputPolarity) -> None:
        """Set the burst gate polarity."""
        raise NotImplementedError(f"set_burst_gate_polarity is not implemented for {type(self).__name__}")

    # --- Arb waveform (CH1 only) ---

    def upload_arb_waveform_float(self, data: list[float]) -> None:
        """Upload a floating-point arb waveform to VOLATILE; values must be −1.0…+1.0."""
        raise NotImplementedError(f"upload_arb_waveform_float is not implemented for {type(self).__name__}")

    def upload_arb_waveform_dac(self, data: list[int]) -> None:
        """Upload an integer arb waveform to VOLATILE; values must be 0…16383 (14-bit)."""
        raise NotImplementedError(f"upload_arb_waveform_dac is not implemented for {type(self).__name__}")

    def set_arb_user(self, channel: Channel, name: str) -> None:
        """Select the arb waveform by name on ``channel``."""
        raise NotImplementedError(f"set_arb_user is not implemented for {type(self).__name__}")

    def copy_arb_to_nonvolatile(self, dest_name: str) -> None:
        """Copy the VOLATILE arb buffer to non-volatile memory under ``dest_name``."""
        raise NotImplementedError(f"copy_arb_to_nonvolatile is not implemented for {type(self).__name__}")

    # --- Coupling / copy ---

    def set_coupling_state(self, enable: bool) -> None:
        """Enable or disable channel coupling."""
        raise NotImplementedError(f"set_coupling_state is not implemented for {type(self).__name__}")

    def set_coupling_base_channel(self, channel: Channel) -> None:
        """Set the coupling base channel."""
        raise NotImplementedError(f"set_coupling_base_channel is not implemented for {type(self).__name__}")

    def set_coupling_phase_deviation(self, deviation_deg: float) -> None:
        """Set the coupling phase deviation (degrees)."""
        raise NotImplementedError(f"set_coupling_phase_deviation is not implemented for {type(self).__name__}")

    def set_coupling_freq_deviation(self, deviation_hz: float) -> None:
        """Set the coupling frequency deviation (Hz)."""
        raise NotImplementedError(f"set_coupling_freq_deviation is not implemented for {type(self).__name__}")

    def copy_channel(self, source: Channel, dest: Channel) -> None:
        """Copy all settings from ``source`` channel to ``dest`` channel."""
        raise NotImplementedError(f"copy_channel is not implemented for {type(self).__name__}")

    # --- System ---

    def set_clock_source(self, source: ClockSource) -> None:
        """Set the reference clock source (internal or external 10 MHz)."""
        raise NotImplementedError(f"set_clock_source is not implemented for {type(self).__name__}")

    @abc.abstractmethod
    def check_errors(self) -> None:
        """Query the instrument error queue and raise on non-zero error code."""
