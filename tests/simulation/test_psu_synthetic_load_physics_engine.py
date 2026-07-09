"""Feature test for Cycle 1: Rust physics engine + PSU synthetic-load integration.

User story: a PSU-simulator user configures the simulated PSU exactly as they do
today against a resistive synthetic load and observes unchanged Ohm's-law
regulation once things have settled. The same user then changes the voltage
setpoint and observes the measured voltage settle *gradually* toward the new
value along a real RC curve (the PSU's own output capacitance, modeled as a
genuine energy-storage element) rather than snapping to it instantly -- a
capability that does not exist before this cycle lands. No waveform-shaped load
is involved: the counterparty is the same resistive-plus-EMF load as today
throughout (this cycle explicitly does not add a waveform-shaped counterparty).

This test fails today: `instro.simulation.physics` does not exist yet, so the
import below fails before any assertion runs -- and even setting that aside,
`scpi_sim_server.py`'s regulation is static per-query Python Ohm's law with no
memory of any kind, so a setpoint change snaps to the new value on the very
first sample; the monotonic-settling assertion would fail on its own too.

See `.ailly/developer/2026-07-08-B-simulation-physics-engine/design.md`.

The exact analytic RC exponential shape is checked at the Rust unit-test level
(deterministic `dt`, no wall-clock jitter); this end-to-end test only checks the
qualitative, real-world-observable shape of the transient, since wall-clock
timing between SCPI round-trips is not precise enough here to fit a curve.
"""

from __future__ import annotations

import time

import pytest

from instro.lib.transports import VisaConfig
from instro.psu.drivers.simulated import SimulatedPSU
from instro.psu.scpi_sim_server import SimulatedLoad, SimulatedPSUServer
from instro.psu.scpi_sim_server import SimulatedPSU as SimulatedPSUSimulator


def test_psu_voltage_settles_gradually_after_setpoint_step() -> None:
    simulator = SimulatedPSUSimulator(num_channels=1)
    # Bind an ephemeral port to avoid EADDRINUSE collisions on shared CI runners.
    server = SimulatedPSUServer(simulator, host="127.0.0.1", port=0)
    server.start()
    driver = SimulatedPSU(VisaConfig(visa_resource=f"TCPIP0::127.0.0.1::{server.port}::SOCKET"))
    driver.open()
    try:
        # --- Given: a resistive synthetic load, exactly as today's simulator supports ---
        # probe_resistance doubles as the engine's r_series (Correction 8): 0.5 ohms is
        # the "Closing Bell" example's own value and gives tau = r_series *
        # capacitance_farads ~= 110 us, comfortably observable against this test's SCPI
        # round-trip sampling cadence. probe_resistance=0.0 would floor r_series to a
        # near-short internal impedance (see effective_conductance in the Rust crate),
        # making the transient settle in ~100 ns -- faster than any query can observe --
        # which would defeat this test's own premise without changing anything about the
        # steady-state regression bar below (still well within its tolerance).
        simulator.channels[0].load = SimulatedLoad(resistance=1000.0, probe_resistance=0.5)
        driver.set_current_limit(1.0, channel=1)
        driver.set_voltage(10.0, channel=1)
        driver.output_enable(True, channel=1)

        # Let any startup transient settle before asserting the steady-state regression bar.
        time.sleep(0.005)

        # Then: unchanged Ohm's-law regulation once settled. Lightly loaded, so voltage
        # sits at setpoint and current is V/R.
        assert driver.get_voltage(channel=1) == pytest.approx(10.0, abs=0.05), (
            "settled PSU voltage against a resistive synthetic load should be unchanged "
            "from today's Ohm's-law regulation after re-platforming onto the physics engine"
        )
        assert driver.get_current(channel=1) == pytest.approx(10.0 / 1000.0, abs=0.01), (
            "settled PSU current against a resistive synthetic load should be unchanged "
            "from today's Ohm's-law regulation after re-platforming onto the physics engine"
        )

        # --- When: the voltage setpoint steps, triggering a real RC transient ---
        v_initial = 10.0
        v_final = 15.0
        driver.set_voltage(v_final, channel=1)

        samples: list[tuple[float, float]] = []
        t0 = time.monotonic()
        for _ in range(20):
            samples.append((time.monotonic() - t0, driver.get_voltage(channel=1)))

        # Then: the very first sample after the step has not yet reached v_final -- proof
        # the engine has real memory (an output capacitance), not an instant algebraic snap.
        first_elapsed, first_voltage = samples[0]
        assert abs(first_voltage - v_final) > 0.5, (
            f"first sample after the setpoint step read {first_voltage}V at "
            f"t={first_elapsed:.6f}s, expected it to still be short of the new setpoint "
            f"({v_final}V) rather than snapping there instantly"
        )

        # And: voltage is monotonically approaching v_final while still meaningfully off
        # target, not jumping around or overshooting. Once within measurement-noise range
        # of v_final (MEASure:VOLTage applies +/-0.5% Gaussian noise -- add_noise in
        # scpi_sim_server.py), gap-to-target bounces on noise alone and monotonicity stops
        # being a meaningful signal, so the check only applies to the still-settling
        # portion of the trace (gap > NOISE_FLOOR), matching the qualitative shape this
        # test cares about rather than an exact curve fit (see module docstring).
        NOISE_FLOOR = 0.2
        gaps = [abs(v_final - voltage) for _, voltage in samples]
        settling_gaps = [gap for gap in gaps if gap > NOISE_FLOOR] or gaps[:1]
        non_increasing = all(settling_gaps[i + 1] <= settling_gaps[i] + 0.02 for i in range(len(settling_gaps) - 1))
        assert non_increasing, (
            f"expected voltage to monotonically approach the new setpoint while still "
            f"settling, observed gaps-to-target over time: {gaps}"
        )

        # And: given enough elapsed wall-clock time (many multiples of a realistic output
        # capacitor's RC time constant), the transient has fully settled to v_final.
        # MEASure:VOLTage applies +/-0.5% Gaussian noise (add_noise in scpi_sim_server.py:
        # std_dev = |value| * 0.005 / 3 ~= 0.025V here), so a single sample against a tight
        # tolerance is flaky by construction -- average several samples to shrink the
        # effective noise instead of widening the tolerance past what's meaningful.
        time.sleep(0.01)
        settled_samples = [driver.get_voltage(channel=1) for _ in range(8)]
        settled_voltage = sum(settled_samples) / len(settled_samples)
        assert settled_voltage == pytest.approx(v_final, abs=0.05), (
            f"expected voltage to have settled to the new setpoint ({v_final}V) well "
            f"after the transient window, measured mean {settled_voltage}V over "
            f"{settled_samples}"
        )
    finally:
        driver.close()
        server.shutdown()
