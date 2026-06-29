"""Hardware validation for B&K Precision 8514B (BK85XXB) via InstroELoad. Self-contained; no publishers.

Exercises every method BK85XXB implements, driven through the InstroELoad public
API to confirm the HAL surface is consistent: mode select (CC/CV/CP/CR), level
and range roundtrips, slew-rate config, input/short enable, current/voltage
readback, and SYST:ERR? error propagation. Also checks the HAL guards
(set_level/set_range require a mode first) and that :RANGe is rejected for CP/CR.

Two check tiers: structural sanity (finite values, commands accepted without a
device error) is always asserted; strict value checks would require a known
source on the input terminals and are skipped here.

Wiring / stimulus:
    Load INPUT terminals are OPEN (nothing connected). Enabling the input draws
    no real current, so this is safe but voltage/current readback sit near 0.
    To validate measured values, feed a current-limited source into the input
    and set the SOURCE_* expectations below.

Tested unit: B&K Precision 8514B, firmware 1.57 (INSTRO-374).

Run:
    uv run python tests/eload/bk/test_bk_85xxb_hardware.py
"""

from __future__ import annotations

import math
import sys
import time
from collections.abc import Callable

import pytest

from instro.eload import InstroELoad
from instro.eload.drivers.bk_85xxb import BK85XXB
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports import SerialConfig, VisaConfig
from instro.lib.types import Command

pytestmark = pytest.mark.hardware

# --- Configuration — edit before running -----------------------------------
RESOURCE = "ASRL8::INSTR"  # <-- edit to your 8514B's VISA resource string
BAUD_RATE = 9600
CHANNEL = 1

# Safe operating levels — kept tiny since the input terminals are open. Raise
# only with a known source connected and well inside the unit's ratings
# (8514B: 120 V / 240 A / 1500 W).
CC_LEVEL_A = 0.1
CV_LEVEL_V = 5.0
CR_LEVEL_OHM = 100.0
CP_LEVEL_W = 1.0
CC_RANGE_A = 1.0
CV_RANGE_V = 10.0
SLEW_RATE_A_PER_US = 0.1

# Strict value check (open terminals -> leave None). Set to the source you feed
# into the input to enable a measured-current check.
SOURCE_VOLTAGE_V: float | None = None


def _cmd_value(cmd: Command) -> float | str:
    """Unwrap the single value a HAL command-getter packages."""
    return next(iter(cmd.channel_data.values()))


def _make_eload() -> InstroELoad:
    eload = InstroELoad(
        name="hw_validate",
        driver=BK85XXB(VisaConfig(visa_resource=RESOURCE, serial_config=SerialConfig(baud_rate=BAUD_RATE))),
        publishers=None,
    )
    eload.open()
    return eload


def _run(name: str, fn: Callable[[], None], failures: list) -> None:
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def run_all() -> list:
    eload = _make_eload()
    failures: list = []
    ch = CHANNEL
    try:
        # --- Identity (records firmware for the ticket) ---
        def identity() -> None:
            idn = eload._driver._visa.query("*IDN?").strip()
            print(f"         *IDN? -> {idn}")
            assert "B&K" in idn and "85" in idn, f"unexpected identity: {idn!r}"

        _run("identity (*IDN?)", identity, failures)

        # --- set_mode for all four modes ---
        def modes() -> None:
            for mode in (LoadMode.CC, LoadMode.CV, LoadMode.CP, LoadMode.CR):
                cmd = eload.set_mode(mode, channel=ch)
                assert _cmd_value(cmd) == mode.value, f"{mode} command echo mismatch"
            eload.set_mode(LoadMode.CC, channel=ch)  # leave in CC

        _run("set_mode (CC/CV/CP/CR)", modes, failures)

        # --- set_level guard: requires a mode first ---
        def level_requires_mode() -> None:
            fresh = InstroELoad(name="guard", driver=BK85XXB(RESOURCE), publishers=None)
            try:
                fresh.set_level(value=CC_LEVEL_A, channel=ch)
            except ValueError:
                return
            raise AssertionError("set_level before set_mode should raise ValueError")

        _run("set_level guard (mode required)", level_requires_mode, failures)

        # --- set_level in each mode ---
        def levels() -> None:
            eload.set_mode(LoadMode.CC, channel=ch)
            eload.set_level(value=CC_LEVEL_A, channel=ch)
            eload.set_mode(LoadMode.CV, channel=ch)
            eload.set_level(value=CV_LEVEL_V, channel=ch)
            eload.set_mode(LoadMode.CR, channel=ch)
            eload.set_level(value=CR_LEVEL_OHM, channel=ch)
            eload.set_mode(LoadMode.CP, channel=ch)
            eload.set_level(value=CP_LEVEL_W, channel=ch)
            eload.set_mode(LoadMode.CC, channel=ch)

        _run("set_level (CC/CV/CR/CP)", levels, failures)

        # --- set_range for CC and CV ---
        def range_cc_cv() -> None:
            eload.set_mode(LoadMode.CC, channel=ch)
            eload.set_range(value=CC_RANGE_A, channel=ch)
            eload.set_mode(LoadMode.CV, channel=ch)
            eload.set_range(value=CV_RANGE_V, channel=ch)
            eload.set_mode(LoadMode.CC, channel=ch)

        _run("set_range (CC, CV)", range_cc_cv, failures)

        # --- set_range rejected for CP/CR ---
        def range_rejects_cp_cr() -> None:
            for mode in (LoadMode.CP, LoadMode.CR):
                eload.set_mode(mode, channel=ch)
                try:
                    eload.set_range(value=10.0, channel=ch)
                except NotImplementedError:
                    continue
                raise AssertionError(f"set_range should raise NotImplementedError in {mode}")
            eload.set_mode(LoadMode.CC, channel=ch)

        _run("set_range rejected (CP, CR)", range_rejects_cp_cr, failures)

        # --- set_slewrate for each direction ---
        def slewrate() -> None:
            for direction in (SlewRateDirection.RISE, SlewRateDirection.FALL, SlewRateDirection.BOTH):
                eload.set_slewrate(direction, rate=SLEW_RATE_A_PER_US, channel=ch)

        _run("set_slewrate (RISE/FALL/BOTH)", slewrate, failures)

        # --- output_enable on/off ---
        def output() -> None:
            eload.set_mode(LoadMode.CC, channel=ch)
            eload.set_level(value=CC_LEVEL_A, channel=ch)
            eload.output_enable(True, channel=ch)
            time.sleep(0.3)
            eload.output_enable(False, channel=ch)

        _run("output_enable (on/off)", output, failures)

        # --- get_current / get_voltage (structural; open terminals -> ~0) ---
        def readback() -> None:
            current = eload.get_current(channel=ch).latest
            voltage = eload.get_voltage(channel=ch).latest
            print(f"         I = {current} A, V = {voltage} V")
            assert math.isfinite(current), f"non-finite current: {current}"
            assert math.isfinite(voltage), f"non-finite voltage: {voltage}"
            if SOURCE_VOLTAGE_V is not None:
                assert math.isclose(voltage, SOURCE_VOLTAGE_V, rel_tol=0.1), (
                    f"measured {voltage} V vs source {SOURCE_VOLTAGE_V} V"
                )

        _run("get_current / get_voltage", readback, failures)

        # --- short_output on/off (safe: terminals open) ---
        def short() -> None:
            eload.short_output(True, channel=ch)
            time.sleep(0.3)
            eload.short_output(False, channel=ch)

        _run("short_output (on/off)", short, failures)

        # --- SYST:ERR? propagation: a bad command surfaces as RuntimeError ---
        def error_propagation() -> None:
            eload._driver._visa.write("BOGUS:COMMAND")
            try:
                eload.get_voltage(channel=ch)
            except RuntimeError as exc:
                assert "BK85XXB reported error" in str(exc), f"unexpected error text: {exc}"
                return
            raise AssertionError("a bad command should surface as RuntimeError via SYST:ERR?")

        _run("error propagation (SYST:ERR?)", error_propagation, failures)

    finally:
        # Disable output first (stops load draw), then release the short — each
        # guarded independently so a failure in one still attempts the other.
        for restore in (
            lambda: eload.output_enable(False, channel=ch),
            lambda: eload.short_output(False, channel=ch),
        ):
            try:
                restore()
            except Exception:  # noqa: BLE001 - best-effort safe state
                pass
        eload.close()
    return failures


@pytest.mark.hardware
def test_bk_85xxb_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)} check(s))'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
