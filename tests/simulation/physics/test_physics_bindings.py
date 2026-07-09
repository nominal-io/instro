"""Tests for the instro.simulation.physics PyO3 binding surface."""

from __future__ import annotations

import pytest

from instro.simulation.physics import Circuit


def _settled_circuit(resistance: float = 1000.0, r_series: float = 0.5) -> Circuit:
    circuit = Circuit()
    circuit.add_psu("psu1", r_series=r_series)
    circuit.attach_synthetic_load("psu1", "resistive", resistance=resistance, emf=0.0)
    circuit.set_psu_voltage_setpoint("psu1", 10.0)
    circuit.set_psu_current_limit("psu1", 1.0)
    circuit.set_psu_output_enabled("psu1", True)
    circuit.step(0.01)
    return circuit


def test_resistive_load_settles_to_ohms_law_voltage() -> None:
    circuit = _settled_circuit()
    assert circuit.psu_voltage("psu1") == pytest.approx(10.0, abs=0.05)
    assert circuit.psu_current("psu1") == pytest.approx(10.0 / 1000.0, abs=0.01)
    assert circuit.psu_mode("psu1") == "CV"


def test_attach_synthetic_load_rejects_unrecognized_kind() -> None:
    circuit = Circuit()
    circuit.add_psu("psu1")
    with pytest.raises(ValueError, match="resistive"):
        circuit.attach_synthetic_load("psu1", "waveform", amplitude=1.0)


def test_set_psu_mode_accepts_cv_and_cc() -> None:
    circuit = Circuit()
    circuit.add_psu("psu1")
    circuit.set_psu_mode("psu1", "CC")
    circuit.set_psu_mode("psu1", "CV")


def test_set_psu_mode_rejects_unrecognized_mode() -> None:
    circuit = Circuit()
    circuit.add_psu("psu1")
    with pytest.raises(ValueError, match="CV"):
        circuit.set_psu_mode("psu1", "CP")


def test_set_psu_current_limit_folds_back_to_cc() -> None:
    circuit = Circuit()
    circuit.add_psu("psu1", r_series=0.5)
    circuit.attach_synthetic_load("psu1", "resistive", resistance=0.1, emf=0.0)
    circuit.set_psu_voltage_setpoint("psu1", 5.0)
    circuit.set_psu_current_limit("psu1", 1.0)
    circuit.set_psu_output_enabled("psu1", True)
    circuit.step(0.01)

    assert circuit.psu_mode("psu1") == "CC"
    assert circuit.psu_current("psu1") == pytest.approx(1.0, abs=1e-6)


def test_set_psu_output_enabled_false_decays_through_the_load() -> None:
    """A disabled output is a zero-amp source, not a zeroed special case (Correction 19c).

    The engine keeps reporting the genuinely solved state: the output capacitor's
    settled voltage decays toward zero through the resistive load over the discharge
    time constant, and the disabled source's own current reads exactly zero. Today's
    user-visible OFF-reads-zero behavior stays gated in Python (_update_channel never
    consults the circuit for a disabled channel).
    """
    circuit = _settled_circuit()
    circuit.set_psu_output_enabled("psu1", False)

    circuit.step(0.001)
    assert circuit.psu_current("psu1") == pytest.approx(0.0, abs=1e-9)
    # Real memory: shortly after disable, the capacitor still holds most of its charge.
    assert circuit.psu_voltage("psu1") > 9.0

    # Discharge time constant is r_load * C = 0.22 s; a few seconds is fully decayed.
    for _ in range(3):
        circuit.step(1.0)
    assert circuit.psu_voltage("psu1") == pytest.approx(0.0, abs=0.01)
    assert circuit.psu_current("psu1") == pytest.approx(0.0, abs=1e-9)


def test_set_psu_remote_sense_eliminates_probe_drop() -> None:
    local = Circuit()
    local.add_psu("psu1", r_series=10.0)
    local.attach_synthetic_load("psu1", "resistive", resistance=100.0, emf=0.0)
    local.set_psu_voltage_setpoint("psu1", 5.0)
    local.set_psu_current_limit("psu1", 1.0)
    local.set_psu_output_enabled("psu1", True)
    # tau = (10 || 100) * 220 uF ~= 2 ms: 50 ms of simulated time is fully settled.
    local.step(0.05)
    i_local = local.psu_current("psu1")

    remote = Circuit()
    remote.add_psu("psu1", r_series=10.0)
    remote.attach_synthetic_load("psu1", "resistive", resistance=100.0, emf=0.0)
    remote.set_psu_voltage_setpoint("psu1", 5.0)
    remote.set_psu_current_limit("psu1", 1.0)
    remote.set_psu_remote_sense("psu1", True)
    remote.set_psu_output_enabled("psu1", True)
    remote.step(0.05)
    i_remote = remote.psu_current("psu1")

    assert i_remote > i_local
    assert i_local == pytest.approx(5.0 / 110.0, rel=0.1)
    assert i_remote == pytest.approx(5.0 / 100.0, rel=0.1)


def test_step_does_not_raise_for_every_reachable_public_configuration() -> None:
    """Documents that `RuntimeError` from `step` is unreachable via this public surface."""
    # SolverError -> RuntimeError (via err.to_string()) is exercised directly at the Rust
    # level (SolverError's Display impl and the SingularJacobian case constructed by hand
    # in instro-simulation-physics-rs's own unit tests). Reaching it through this Python
    # surface would need two components pinning the *same* node pair to conflicting fixed
    # voltages, but the closed selector surface never wires that: each PSU stamps one
    # regulating source, each resistive attachment's EMF source sits behind its own
    # counterparty node, and every resistance is floored/ceilinged away from literal
    # 0/inf (see effective_conductance), so every configuration reachable from
    # add_psu/attach_synthetic_load/set_psu_* stays linear and non-singular, and the
    # backward-Euler Newton solve converges.
    circuit = Circuit()
    circuit.add_psu("psu1", r_series=0.0)
    circuit.attach_synthetic_load("psu1", "resistive", resistance=0.0, emf=7.0)
    circuit.set_psu_voltage_setpoint("psu1", 5.0)
    circuit.set_psu_current_limit("psu1", 1000.0)
    circuit.set_psu_output_enabled("psu1", True)

    circuit.step(0.01)  # does not raise
