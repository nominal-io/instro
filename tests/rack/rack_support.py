"""Shared constants, helpers, and runner for the NYC test rack hardware suites.

DIN netlist under test (pre-relay bring-up, before the DAQ tray is installed):
    BUS+   blocks 1-2, 11    PSU CH1+, eload +, DMM HI, top of the 100k/10k divider
    GND    blocks 3, 6-10    PSU CH1-, PSU CH2-, eload -, DMM LO, divider bottom, DIN earth
    VDIV   blocks 4-5        divider tap (11:1); nothing reads it until the DAQs land
    LOOP   blocks 12-13      PSU CH2+ -> 100 Ω 5 W -> DMM 3A input (returns via DMM LO)
    CH3    unconnected

So the DMM reads the bus in DCV and the CH2 loop current in DCI.

Every check starts from and returns to a safe state (eload input off, all PSU outputs
off). Each suite publishes to its own JSONL FilePublisher under
tests/rack/captures/<run_id>/, next to discovery.json and the full DEBUG log.

Instruments are discovered, not configured: see discovery.py for how VISA and serial ports are
probed and matched to in-tree drivers, and the RACK_* environment overrides.

Run every rack suite:
    uv run python tests/rack/rack_support.py            # extra args go to pytest
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar

import pytest
from discovery import DiscoveredInstrument

from instro.dmm import InstroDMM
from instro.eload import InstroELoad
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.types import Measurement
from instro.psu import InstroPSU

logger = logging.getLogger("rack")

RACK_DIR = Path(__file__).parent
CAPTURE_ROOT = RACK_DIR / "captures"

# Channel roles fixed by the rack wiring. A PSU with fewer channels skips the checks that need
# the missing role (require_channel), so a 1-channel supply still runs every bus check.
BUS_CH = 1
LOOP_CH = 2
SPARE_CH = 3  # unconnected

BUS_VOLTAGE_V = 5.0
BUS_CURRENT_LIMIT_A = 1.0
BUS_SWEEP_V = (1.0, 2.5, 5.0)
BUS_DIVIDER_OHM = 110e3  # 100k + 10k permanently across the bus

LOOP_CURRENT_LIMIT_A = 0.05
LOOP_SWEEP_V = (1.0, 2.0, 3.0)  # ~10/20/30 mA through the 100 Ω loop resistor
LOOP_R_NOMINAL_OHM = 100.0
LOOP_R_REL_TOL = 0.10  # 5 W power resistor tolerance plus the DMM current-shunt burden
# The DMM shunt is in series with the loop, so auto-range would change the loop current between
# reads (a low range's shunt roughly halves it); a fixed range keeps the loop resistance constant.
LOOP_DCI_RANGE_A = 0.1

ELOAD_CC_STEPS_A = (0.1, 0.25, 0.5)
ELOAD_CC_RANGE_A = 3.0
ELOAD_CR_OHM = 25.0

# Protection trips well above anything the checks program, so a wiring fault trips the PSU.
# Armed only where the PSU supports it and the channel exists.
OVP_V = {BUS_CH: 7.0, LOOP_CH: 7.0}
OCP_A = {BUS_CH: 1.2, LOOP_CH: 0.1}

# A PSU's own readback can trail its real output: the DP832A reports 0 V for 0.5-2.3 s after
# :OUTP ON while a meter on the terminals already reads the setpoint. Turn-on checks poll the
# readback instead of sleeping; SETTLE_S only covers level steps on an already-live output.
READBACK_TIMEOUT_S = 5.0
READBACK_POLL_S = 0.25
# Voltage and current readbacks don't update together: right after a step the current can still be
# mid-transient (27.5 mA reported vs 19.5 mA real), so current is read twice, one refresh apart.
READBACK_REFRESH_S = 0.6
CURRENT_STABLE_A = 0.0005
SETTLE_S = 2.0
OFF_THRESHOLD_V = 0.05
OFF_THRESHOLD_A = 0.0005
POLL_INTERVAL_S = 0.25
POLL_DURATION_S = 3.0


@dataclasses.dataclass
class Rack:
    psu: InstroPSU
    dmm: InstroDMM
    eload: InstroELoad
    capture_path: Path
    found: dict[str, DiscoveredInstrument]

    @property
    def instruments(self) -> tuple[InstroPSU, InstroDMM, InstroELoad]:
        return (self.psu, self.dmm, self.eload)

    @property
    def psu_channels(self) -> range:
        """The PSU's channels, from the channel count discovery matched for its model."""
        count = self.found["psu"].num_channels
        assert count is not None, "discovery registry gives every PSU a channel count"
        return range(1, count + 1)

    @property
    def psu_current_readback(self) -> tuple[float, float]:
        """±(fraction, amperes) accuracy of the PSU's current readback, from the discovery registry."""
        return self.found["psu"].current_readback

    def safe_state(self) -> None:
        safe_state(self.psu, self.eload, self.psu_channels)


# Calls into an optional capability raise one of these when the instrument or driver lacks it.
UNSUPPORTED = (NotImplementedError, FeatureNotSupportedError)
T = TypeVar("T")


def require(call: Callable[[], T], capability: str) -> T:
    """Run ``call``; skip the check if the instrument doesn't support ``capability``."""
    try:
        return call()
    except UNSUPPORTED as exc:
        pytest.skip(f"{capability} not supported: {type(exc).__name__}: {exc}")


def optional(call: Callable[[], T], capability: str) -> T | None:
    """Run ``call``; return None (and log it) if the instrument doesn't support ``capability``."""
    try:
        return call()
    except UNSUPPORTED as exc:
        logger.info("  n/a   %s: %s", capability, exc)
        return None


def require_channel(rack: Rack, channel: int, role: str) -> None:
    if channel not in rack.psu_channels:
        pytest.skip(f"{role} is wired to CH{channel}; this PSU has {len(rack.psu_channels)} channel(s)")


def assert_close(label: str, measured: float, expected: float, rel: float, abs_: float) -> None:
    tol = abs_ + rel * abs(expected)
    ok = math.isfinite(measured) and abs(measured - expected) <= tol
    logger.info(
        "  %s  %-34s measured=%9.4f  expected=%9.4f  tol=±%.4f",
        "PASS" if ok else "FAIL",
        label,
        measured,
        expected,
        tol,
    )
    assert ok, f"{label}: measured {measured:.4f}, expected {expected:.4f} ± {tol:.4f}"


def assert_below(label: str, measured: float, limit: float) -> None:
    ok = math.isfinite(measured) and abs(measured) <= limit
    logger.info("  %s  %-34s measured=%9.4f  limit=±%.4f", "PASS" if ok else "FAIL", label, measured, limit)
    assert ok, f"{label}: |{measured:.4f}| exceeds {limit:.4f}"


def latest(measurement: Measurement | None) -> float:
    assert measurement is not None, "instrument returned no measurement"
    return float(measurement.latest)


def wait_for_psu_readback(psu: InstroPSU, channel: int, expected: float, rel: float, abs_: float) -> float:
    """Poll the PSU's voltage readback until it reaches ``expected`` (V); fail if it doesn't within the timeout."""
    tol = abs_ + rel * abs(expected)
    start = time.perf_counter()
    while True:
        measured = latest(psu.get_voltage(channel=channel))
        elapsed = time.perf_counter() - start
        if math.isfinite(measured) and abs(measured - expected) <= tol:
            logger.info(
                "  PASS  PSU CH%d readback settled            measured=%9.4f  expected=%9.4f  after %.2f s",
                channel,
                measured,
                expected,
                elapsed,
            )
            return measured
        if elapsed >= READBACK_TIMEOUT_S:
            logger.info(
                "  FAIL  PSU CH%d readback never settled       measured=%9.4f  expected=%9.4f",
                channel,
                measured,
                expected,
            )
            raise AssertionError(
                f"PSU CH{channel} readback stuck at {measured:.4f} V, expected {expected:.4f} ± {tol:.4f} "
                f"after {READBACK_TIMEOUT_S} s (output status: "
                f"{'ON' if latest(psu.get_output_status(channel=channel)) else 'OFF'})"
            )
        time.sleep(READBACK_POLL_S)


def wait_for_stable_psu_current(psu: InstroPSU, channel: int) -> float:
    """Return the PSU's current readback (A) once two reads one refresh apart agree; fail after the timeout."""
    start = time.perf_counter()
    previous = latest(psu.get_current(channel=channel))
    while True:
        time.sleep(READBACK_REFRESH_S)
        current = latest(psu.get_current(channel=channel))
        elapsed = time.perf_counter() - start
        if abs(current - previous) <= CURRENT_STABLE_A:
            logger.info("  PSU CH%d current readback stable at %.4f A after %.2f s", channel, current, elapsed)
            return current
        logger.info("  PSU CH%d current readback still moving: %.4f -> %.4f A", channel, previous, current)
        if elapsed >= READBACK_TIMEOUT_S:
            raise AssertionError(f"PSU CH{channel} current readback never stabilized within {READBACK_TIMEOUT_S} s")
        previous = current


def psu_mode(psu: InstroPSU, channel: int) -> str | None:
    """``InstroPSU.get_operating_mode`` value ("CV", "CC", "OFF", ...), or None if the driver doesn't report it."""
    measurement = optional(lambda: psu.get_operating_mode(channel=channel), f"PSU CH{channel} operating mode")
    return None if measurement is None else str(measurement.latest)


def assert_mode(psu: InstroPSU, channel: int, expected: str, why: str) -> None:
    """Assert the PSU's regulation mode when its driver reports one; otherwise log that it wasn't checked."""
    mode = psu_mode(psu, channel)
    if mode is None:
        logger.info("  n/a   CH%d mode not checked (expected %s: %s)", channel, expected, why)
        return
    logger.info("  %s  PSU CH%d mode %s (expected %s)", "PASS" if mode == expected else "FAIL", channel, mode, expected)
    assert mode == expected, f"PSU CH{channel} should be {expected} ({why}), got {mode}"


def arm_protection(psu: InstroPSU, channels: range) -> None:
    """Arm OVP/OCP at the rack levels on each of ``channels``, skipping what the PSU doesn't support."""
    logger.info("Arming PSU protection where supported: OVP %s V, OCP %s A", OVP_V, OCP_A)
    for ch, level in OVP_V.items():
        if ch in channels:
            optional(lambda: psu.set_overvoltage_protection_level(level, channel=ch), f"CH{ch} OVP level")
            optional(lambda: psu.set_overvoltage_protection_enabled(True, channel=ch), f"CH{ch} OVP enable")
    for ch, level in OCP_A.items():
        if ch in channels:
            optional(lambda: psu.set_overcurrent_protection_level(level, channel=ch), f"CH{ch} OCP level")
            optional(lambda: psu.set_overcurrent_protection_enabled(True, channel=ch), f"CH{ch} OCP enable")


def safe_state(psu: InstroPSU, eload: InstroELoad, channels: range) -> None:
    """Eload input off first (stop the draw), then every PSU output off; each step best-effort."""
    try:
        eload.output_enable(False)
    except Exception:
        logger.exception("safe_state: failed to disable eload input")
    for ch in channels:
        try:
            psu.output_enable(False, channel=ch)
        except Exception:
            logger.exception("safe_state: failed to disable PSU CH%d", ch)
    logger.info("safe_state: eload input OFF, PSU CH%s OFF", "-".join(str(c) for c in (channels[0], channels[-1])))


def read_capture(path: Path, check: str) -> list[dict]:
    """Records the named check published, in publish order."""
    with path.open() as f:
        records = [json.loads(line) for line in f]
    return [r for r in records if r.get("tags", {}).get("check") == check]


def run_pytest(target: str | Path) -> int:
    """Run ``target`` under pytest with live INFO logs and a DEBUG log file in a fresh run directory."""
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.environ["RACK_RUN_ID"] = run_id
    run_dir = CAPTURE_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return pytest.main(
        [
            str(target),
            "-m",
            "hardware",
            "-v",
            "-p",
            "no:cacheprovider",
            "-o",
            "log_cli=true",
            "--log-cli-level=INFO",
            "--log-cli-format=%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s",
            "--log-cli-date-format=%H:%M:%S",
            f"--log-file={run_dir / 'rack.log'}",
            "--log-file-level=DEBUG",
            "--log-file-format=%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s",
            "--log-file-date-format=%H:%M:%S",
            *sys.argv[1:],
        ]
    )


if __name__ == "__main__":
    sys.exit(run_pytest(RACK_DIR))
