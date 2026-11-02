"""Tests for the transport-free VirtualAWG driver."""

from __future__ import annotations

import pytest

from instro.awg import (
    AmplitudeMeasurementUnit,
    BurstTriggerSource,
    BurstType,
    GatePolarity,
    InstroAWG,
    ModulationType,
    Sine,
    Square,
    SweepTriggerSource,
    SweepType,
)
from instro.awg.drivers import VirtualAWG


def test_starts_in_reset_state() -> None:
    awg = InstroAWG(name="awg", driver=VirtualAWG(), num_channels=2)
    with awg:
        assert awg.get_waveform(2) == Sine(frequency_hz=1000.0)
        assert awg.get_amplitude(2) == (0.1, AmplitudeMeasurementUnit.VPP)
        assert awg.get_offset(2).latest == 0.0
        assert awg.get_output_state(2).latest == 0.0
        assert awg.get_output_load(2).latest == 50.0


def test_every_setting_round_trips_through_instro_awg() -> None:
    awg = InstroAWG(name="awg", driver=VirtualAWG(num_channels=2), num_channels=2)
    with awg:
        awg.set_waveform(2, Square(frequency_hz=5000.0, duty_cycle_pct=25.0))
        awg.set_amplitude(2, 1.5, AmplitudeMeasurementUnit.VRMS)
        awg.set_offset(2, -0.25)
        awg.set_output_load(2, None)
        awg.output_enable(2, True)
        awg.align_phase()
        awg.set_modulation(2, ModulationType.FM, Sine(frequency_hz=10.0), 100.0)
        awg.modulation_enable(2, True)
        awg.set_burst(2, BurstType.GATED)
        awg.set_burst_trigger(2, BurstTriggerSource.MANUAL)
        awg.set_burst_delay(2, 0.002)
        awg.set_burst_gate_polarity(2, GatePolarity.INV)
        awg.set_burst_ncycles(2, 7)
        awg.set_burst_period(2, 0.5)
        awg.burst_enable(2, True)
        awg.fire_burst_trigger(2)
        awg.set_sweep(2, SweepType.LOG)
        awg.set_sweep_trigger(2, SweepTriggerSource.MANUAL)
        awg.set_sweep_start_freq(2, 20.0)
        awg.set_sweep_end_freq(2, 20000.0)
        awg.set_sweep_time(2, 3.0)
        awg.set_sweep_start_hold_time(2, 0.1)
        awg.set_sweep_stop_hold_time(2, 0.2)
        awg.set_sweep_return_time(2, 0.3)
        awg.sweep_enable(2, True)
        awg.fire_sweep_trigger(2)

        assert awg.get_waveform(2) == Square(frequency_hz=5000.0, duty_cycle_pct=25.0)
        assert awg.get_amplitude(2) == (1.5, AmplitudeMeasurementUnit.VRMS)
        assert awg.get_offset(2).latest == -0.25
        assert awg.get_output_load(2).latest == float("inf")
        assert awg.get_output_state(2).latest == 1.0
        assert awg.get_modulation_type(2).latest == "FM"
        assert awg.get_modulation_state(2).latest == 1.0
        assert awg.get_burst_type(2).latest == "GATED"
        assert awg.get_burst_trigger(2).latest == "MAN"
        assert awg.get_burst_delay(2).latest == 0.002
        assert awg.get_burst_gate_polarity(2).latest == "INV"
        assert awg.get_burst_ncycles(2).latest == 7.0
        assert awg.get_burst_period(2).latest == 0.5
        assert awg.get_burst_state(2).latest == 1.0
        assert awg.get_sweep_type(2).latest == "LOG"
        assert awg.get_sweep_trigger(2).latest == "MAN"
        assert awg.get_sweep_start_freq(2).latest == 20.0
        assert awg.get_sweep_end_freq(2).latest == 20000.0
        assert awg.get_sweep_time(2).latest == 3.0
        assert awg.get_sweep_start_hold_time(2).latest == 0.1
        assert awg.get_sweep_stop_hold_time(2).latest == 0.2
        assert awg.get_sweep_return_time(2).latest == 0.3
        assert awg.get_sweep_state(2).latest == 1.0
        assert awg.get_waveform(1) == Sine(frequency_hz=1000.0)


def test_fire_trigger_requires_a_manual_source() -> None:
    awg = InstroAWG(name="awg", driver=VirtualAWG(), num_channels=2)
    with awg:
        awg.burst_enable(1, True)
        with pytest.raises(ValueError, match="MANUAL"):
            awg.fire_burst_trigger(1)
        awg.sweep_enable(1, True)
        with pytest.raises(ValueError, match="MANUAL"):
            awg.fire_sweep_trigger(1)
