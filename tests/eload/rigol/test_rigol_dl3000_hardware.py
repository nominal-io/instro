"""Hardware validation for Rigol DL3021 (RigolDL3000) via InstroELoad. Self-contained; no publishers.

Exercises every method RigolDL3000 implements, driven through the InstroELoad public
API to confirm the HAL surface is consistent: mode select (CC/CV/CP/CR), level
and range roundtrips, slew-rate config, input enable, current/voltage readback,
and :SYST:ERR? error propagation. Also checks the HAL guards (set_level/set_range
require a mode first), that :RANG is rejected for CP, and that short_output is
rejected (the DL3000 has no short-circuit SCPI command).

Two check tiers: structural sanity (finite values, commands accepted without a
device error) is always asserted; strict value checks would require a known
source on the input terminals and are skipped here.

Wiring / stimulus:
    Load INPUT terminals are OPEN (nothing connected). Enabling the input draws
    no real current, so this is safe but voltage/current readback sit near 0.
    To validate measured values, feed a current-limited source into the input
    and set the SOURCE_* expectations below.

Tested unit: Rigol DL3021, firmware 00.01.05.00.01.

Run:
    uv run python tests/eload/rigol/test_rigol_dl3000_hardware.py
"""

from __future__ import annotations

import math
import sys
import time
from collections.abc import Callable

import pytest

from instro.eload import InstroELoad
from instro.eload.drivers.rigol_dl3000 import RigolDL3000
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.types import Command

pytestmark = pytest.mark.hardware

# --- Configuration — edit before running -----------------------------------
RESOURCE = "USB0::0x1AB1::0x0E11::DL3A286M00348::INSTR"
CHANNEL = 1

# Safe operating levels — kept tiny since the input terminals are open. Raise
# only with a known source connected and well inside the unit's ratings
# (DL3021: 150 V / 40 A / 200 W).
CC_LEVEL_A = 0.1
CV_LEVEL_V = 5.0
CR_LEVEL_OHM = 100.0
CP_LEVEL_W = 1.0
CC_RANGE_A = 1.0
CV_RANGE_V = 10.0
CR_RANGE_OHM = 1000.0  # must cover CR_LEVEL_OHM: the load rejects a CR range below the present CR level
SLEW_RATE_A_PER_US = 0.1

# Strict value check (open terminals -> leave None). Set to the source you feed
# into the input to enable a measured-current check.
SOURCE_VOLTAGE_V: float | None = None


def _cmd_value(cmd: Command) -> float | str:
    """Unwrap the single value a HAL command-getter packages."""
    return next(iter(cmd.channel_data.values()))


def _make_eload() -> InstroELoad:
    eload = InstroELoad(name="hw_validate", driver=RigolDL3000(RESOURCE), publishers=None)
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
            assert "RIGOL" in idn and "DL30" in idn, f"unexpected identity: {idn!r}"

        _run("identity (*IDN?)", identity, failures)

        # --- set_mode for all four modes (verified against the device's queried function) ---
        def modes() -> None:
            for mode in (LoadMode.CC, LoadMode.CV, LoadMode.CP, LoadMode.CR):
                cmd = eload.set_mode(mode, channel=ch)
                assert _cmd_value(cmd) == mode.value, f"{mode} command echo mismatch"
                # set_mode already error-checks via :SYST:ERR?; also confirm the device
                # actually switched function. :SOUR:FUNC? answers CC/CV/CP/CR.
                active = eload._driver._visa.query(":SOUR:FUNC?").strip().upper()
                assert active == mode.value, f"{mode}: device reports {active!r}, expected {mode.value!r}"
            eload.set_mode(LoadMode.CC, channel=ch)  # leave in CC

        _run("set_mode (CC/CV/CP/CR)", modes, failures)

        # --- set_level guard: requires a mode first ---
        def level_requires_mode() -> None:
            fresh = InstroELoad(name="guard", driver=RigolDL3000(RESOURCE), publishers=None)
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

        # --- set_range for CC, CV, and CR ---
        def range_cc_cv_cr() -> None:
            eload.set_mode(LoadMode.CC, channel=ch)
            eload.set_range(value=CC_RANGE_A, channel=ch)
            eload.set_mode(LoadMode.CV, channel=ch)
            eload.set_range(value=CV_RANGE_V, channel=ch)
            eload.set_mode(LoadMode.CR, channel=ch)
            eload.set_range(value=CR_RANGE_OHM, channel=ch)
            eload.set_mode(LoadMode.CC, channel=ch)

        _run("set_range (CC, CV, CR)", range_cc_cv_cr, failures)

        # --- set_range rejected for CP ---
        def range_rejects_cp() -> None:
            eload.set_mode(LoadMode.CP, channel=ch)
            try:
                eload.set_range(value=10.0, channel=ch)
            except FeatureNotSupportedError:
                eload.set_mode(LoadMode.CC, channel=ch)
                return
            raise AssertionError("set_range should raise FeatureNotSupportedError in CP")

        _run("set_range rejected (CP)", range_rejects_cp, failures)

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

        # --- short_output rejected (no short-circuit SCPI command) ---
        def short() -> None:
            try:
                eload.short_output(True, channel=ch)
            except FeatureNotSupportedError:
                return
            raise AssertionError("short_output should raise FeatureNotSupportedError")

        _run("short_output rejected", short, failures)

        # --- :SYST:ERR? propagation: a bad command surfaces as RuntimeError ---
        def error_propagation() -> None:
            eload._driver._visa.write("BOGUS:COMMAND")
            try:
                eload.get_voltage(channel=ch)
            except RuntimeError as exc:
                assert "Rigol DL3000 reported error" in str(exc), f"unexpected error text: {exc}"
                return
            raise AssertionError("a bad command should surface as RuntimeError via :SYST:ERR?")

        _run("error propagation (:SYST:ERR?)", error_propagation, failures)

    finally:
        try:
            eload.output_enable(False, channel=ch)
        except Exception:  # noqa: BLE001 - best-effort safe state
            pass
        eload.close()
    return failures


@pytest.mark.hardware
def test_rigol_dl3000_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)} check(s))'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
