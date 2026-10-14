"""Hardware validation for Rigol DL30xx DC eload driver."""

from __future__ import annotations

import csv
import math
import sys
import time
from collections.abc import Callable

import pytest

from instro.eload import InstroELoad
from instro.eload.drivers.rigol_dl30xx import RigolDL30xx
from instro.eload.types import LoadMode, SlewRateDirection

RESOURCE = "USB0::0x1AB1::0x0E11::DL3A286M00348::INSTR"  # change to VISA resource string

# Params for testing without DUT
CC_TEST_CURRENT = 0.1  # A
CV_TEST_VOLTAGE = 1.0  # V
CR_TEST_RESISTANCE = 1000.0  # ohms
CP_TEST_POWER = 1.0  # W

# None if open terminals
EXPECTED_CC_CURRENT: float | None = None  # A
EXPECTED_CV_VOLTAGE: float | None = None  # V
VALUE_REL_TOL = 0.05

# For confirming test results visually after each step
SLEEP_TIME = 1

# For running battery discharge test
DISCHARGE_BATTERY = False


def _make_eload() -> InstroELoad:
    eload = InstroELoad(name="test_rigol_dl30xx", driver=RigolDL30xx(RESOURCE), publishers=None)
    eload.open()
    return eload


def _run(name: str, fxn: Callable[[], None], failures: list) -> None:
    try:
        fxn()
        print(f"PASS: {name}")
    except Exception as e:
        print(f"FAIL: {name}: {e}")
        failures.append((name, e))


def _skip(name: str, reason: str) -> None:
    print(f"SKIP: {name} bc {reason}")


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError(f"{label}: expected numeric reading, got {value}")
    fvalue = float(value)
    if not math.isfinite(fvalue):
        raise AssertionError(f"{label}: non-finite reading: {fvalue}")
    return fvalue


def run_all() -> list:
    eload = _make_eload()
    driver = eload._driver
    failures: list = []
    ch = 1
    try:
        # Connect to eload
        def idn() -> None:
            idn = driver._visa.query("*IDN?").strip()
            print(f"IDN: {idn}")
            assert "RIGOL" in idn and "DL30" in idn, f"unexpected identity: {idn!r}"

        _run("connection/*IDN?", idn, failures)

        # set_level requires a mode first
        def level_requires_mode() -> None:
            fresh = InstroELoad(name="guard", driver=RigolDL30xx(RESOURCE), publishers=None)
            try:
                fresh.set_level(CC_TEST_CURRENT, channel=ch)
            except ValueError:
                return
            raise AssertionError("set_level before set_mode should raise ValueError")

        _run("set_level guard: mode required", level_requires_mode, failures)

        # Test LoadModes + level + measure
        def check_mode(mode: LoadMode, level: float, expected: float | None) -> None:
            eload.set_mode(mode, channel=ch)
            active = driver._visa.query("FUNCtion?").strip()
            assert active == mode.value, f"{mode.value}: device reports {active!r}"
            eload.set_level(level, channel=ch)
            eload.output_enable(True, channel=ch)
            time.sleep(SLEEP_TIME)

            current = _finite(eload.get_current(channel=ch).latest, f"{mode.value} current")
            voltage = _finite(eload.get_voltage(channel=ch).latest, f"{mode.value} voltage")
            print(f"{mode.value} @ {level} -> current={current:g} A, voltage={voltage:g} V")

            if expected is not None:
                measured = current if mode is LoadMode.CC else voltage
                assert math.isclose(measured, expected, rel_tol=VALUE_REL_TOL), (
                    f"{mode.value}: {measured:g} not within {VALUE_REL_TOL:.0%} of {expected:g}"
                )
            eload.output_enable(False, channel=ch)

        _run(
            "CC mode: set_mode + set_level + measure",
            lambda: check_mode(LoadMode.CC, CC_TEST_CURRENT, EXPECTED_CC_CURRENT),
            failures,
        )
        _run(
            "CV mode: set_mode + set_level + measure",
            lambda: check_mode(LoadMode.CV, CV_TEST_VOLTAGE, EXPECTED_CV_VOLTAGE),
            failures,
        )
        _run(
            "CR mode: set_mode + set_level + measure",
            lambda: check_mode(LoadMode.CR, CR_TEST_RESISTANCE, None),
            failures,
        )
        _run(
            "CP mode: set_mode + set_level + measure",
            lambda: check_mode(LoadMode.CP, CP_TEST_POWER, None),
            failures,
        )

        # set_range is rejected in CP mode, which has no :RANGe command
        def range_rejects_cp() -> None:
            eload.set_mode(LoadMode.CP, channel=ch)
            try:
                eload.set_range(10.0, channel=ch)
            except NotImplementedError:
                return
            raise AssertionError("set_range should raise NotImplementedError in CP")

        _run("set_range rejected: CP", range_rejects_cp, failures)

        # Set range (CV/CR/CC), ending in CC. CR rejects a range below its present level, so first set a level
        # that fits the low range.
        def set_range() -> None:
            for mode, level, low, high in (
                (LoadMode.CV, CV_TEST_VOLTAGE, 15.0, 150.0),
                (LoadMode.CR, 10.0, 15.0, 15000.0),
                (LoadMode.CC, CC_TEST_CURRENT, 6.0, 60.0),
            ):
                eload.set_mode(mode, channel=ch)
                eload.set_level(level, channel=ch)
                eload.set_range(low, channel=ch)
                time.sleep(SLEEP_TIME)
                eload.set_range(high, channel=ch)
                time.sleep(SLEEP_TIME)

        _run("set_range: CV/CR/CC low + high range", set_range, failures)

        # short_output is not implemented: :SYSTem:KEY 33 only toggles the short and its state cannot be queried
        def short() -> None:
            try:
                eload.short_output(True, channel=ch)
            except NotImplementedError:
                return
            raise AssertionError("short_output should raise NotImplementedError")

        _run("short_output: not implemented", short, failures)

        # Set params for CC/CV/CR/CP
        def cc_params() -> None:
            driver.set_cc_params(v_on=1.0, v_limit=150.0, i_limit=10.0)
            time.sleep(SLEEP_TIME)

        _run("set_cc_params:", cc_params, failures)

        # Able to visually verify via screen switches up until this point

        def cv_params() -> None:
            driver.set_cv_params(v_limit=150.0, i_limit=10.0)
            time.sleep(SLEEP_TIME)

        _run("set_cv_params:", cv_params, failures)

        def cr_params() -> None:
            driver.set_cr_params(v_limit=150.0, i_limit=10.0)
            time.sleep(SLEEP_TIME)

        _run("set_cr_params:", cr_params, failures)

        def cp_params() -> None:
            driver.set_cp_params(v_limit=150.0, i_limit=10.0)
            time.sleep(SLEEP_TIME)

        _run("set_cp_params:", cp_params, failures)

        # Set slew rate
        def slew() -> None:
            eload.set_slewrate(SlewRateDirection.BOTH, rate=0.1, channel=ch)
            time.sleep(SLEEP_TIME)
            # Select transient mode for POS/NEG slew to take effect
            driver._visa.write("CURR:TRAN:MODE CONT")
            eload.set_slewrate(SlewRateDirection.RISE, rate=0.1, channel=ch)
            time.sleep(SLEEP_TIME)
            eload.set_slewrate(SlewRateDirection.FALL, rate=0.1, channel=ch)
            time.sleep(SLEEP_TIME)

        _run("set_slewrate: RISE/FALL/BOTH", slew, failures)

        # Set transient params
        def transient() -> None:
            driver.set_transient_curr_params(mode="TOGG", a_level=1.0, b_level=0.1, a_width=1.0, b_width=1.0)
            time.sleep(SLEEP_TIME)
            driver.set_transient_trigger("ON")
            time.sleep(SLEEP_TIME)
            driver.set_transient_trigger("OFF")
            driver._visa.write("INP 0")  # explicitly ensure input is off before moving on
            time.sleep(SLEEP_TIME)

        _run("set_transient_curr_params + set_transient_trigger", transient, failures)

        # Set trigger source + actually trigger
        def trig_source() -> None:
            driver.set_trigger_source("BUS")
            time.sleep(SLEEP_TIME)
            driver.trigger()
            time.sleep(SLEEP_TIME)
            # turn off input and trigger
            driver._visa.write("TRAN:STAT 0")
            driver._visa.write("INP 0")
            # return to default
            driver.set_trigger_source("MANual")
            time.sleep(SLEEP_TIME)

        _run("set_trigger_source + trigger", trig_source, failures)

        # Set list mode + config individual steps
        def list_mode() -> None:
            eload.set_mode(LoadMode.CC, channel=ch)
            driver.set_list_params(mode=LoadMode.CC, range=6.0, count=2, step=3, end_state="LAST")
            driver.set_list_step_params(step_num=0, level=0.5, width=1.0, slew=0.1)
            driver.set_list_step_params(step_num=1, level=1.0, width=1.0, slew=0.1)
            driver.set_list_step_params(step_num=2, level=0.5, width=1.0, slew=0.1)

        _run("set_list_params + set_list_step_params", list_mode, failures)

        # Set function mode. FUNCtion:MODE? only reports WAV (fixed CC/CV/CR/CP), LIST, or BATT (battery, OCP,
        # OPP), and FUNCtion:MODE FIXed/WAVe leave it unchanged, so reset to fixed CC (WAV) before each check.
        def function_mode() -> None:
            results = []
            mode_to_expected = {"LIST": "LIST", "BATTery": "BATT", "OCP": "BATT", "OPP": "BATT"}

            for mode, expected in mode_to_expected.items():
                eload.set_mode(LoadMode.CC, channel=ch)
                baseline = driver._visa.query("FUNCtion:MODE?").strip()
                driver.set_function_mode(mode)
                time.sleep(SLEEP_TIME)
                actual = driver._visa.query("FUNCtion:MODE?").strip()
                results.append((mode, baseline, expected, actual))

            mismatches = [r for r in results if r[1] != "WAV" or r[2] != r[3]]
            assert not mismatches, f"function_mode mismatches (mode, baseline, expected, actual): {mismatches}"

            # FIXed and WAVe leave FUNCtion:MODE? unchanged, so only check that the instrument accepts them
            driver.set_function_mode("WAVe")
            driver.set_function_mode("FIXed")

        _run("set_function_mode: LIST + BATTery + OCP + OPP from fixed CC", function_mode, failures)

        # set_ocp/opp/wave_params only write settings and do not change FUNCtion:MODE, so there is no mode readback
        def ocp_params() -> None:
            driver.set_ocp_params(i_set=0.5, i_step=0.1, i_delay_step=500, i_max=1.0, i_min=0.1)
            time.sleep(SLEEP_TIME)

        _run("set_ocp_params:", ocp_params, failures)

        def opp_params() -> None:
            driver.set_opp_params(p_set=1.0, p_step=0.5, p_delay_step=500, p_max=10.0, p_min=1.0)
            time.sleep(SLEEP_TIME)

        _run("set_opp_params:", opp_params, failures)

        def wave_params() -> None:
            driver.set_wave_params(time="ADD", t_step=1)
            time.sleep(SLEEP_TIME)

        _run("set_wave_params: TIMe + TSTep", wave_params, failures)

        # Set sense state
        def sense() -> None:
            driver.set_sense_state("ON")
            time.sleep(SLEEP_TIME)
            state = int(driver._visa.query("SENSe?"))
            assert state == 1, f"expected SENSe? to return 1, got {state}"

            driver.set_sense_state("OFF")
            time.sleep(SLEEP_TIME)
            state = int(driver._visa.query("SENSe?"))
            assert state == 0, f"expected SENSe? to return 1, got {state}"

        _run("set_sense_state: enable + disable", sense, failures)

        # A bad command surfaces as RuntimeError via SYST:ERR?
        def error_propagation() -> None:
            driver._visa.write("BOGUS:COMMAND")
            try:
                eload.get_voltage(channel=ch)
            except RuntimeError as e:
                assert "Rigol DL30xx reported error" in str(e), f"unexpected error text: {e}"
                return
            raise AssertionError("a bad command should surface as RuntimeError via SYST:ERR?")

        _run("error propagation: SYST:ERR?", error_propagation, failures)

        def battery_discharge() -> None:
            # Tested with single 21700 Li-ion cell
            driver.output_enable(False, channel=ch)

            resting_voltage = _finite(driver._query_checked_float("MEAS:VOLT?"), "battery resting voltage")
            print(f"Resting voltage: {resting_voltage:g} V")

            driver.set_cc_params(v_limit=5, i_limit=6)
            driver.set_function_mode("BATTery")
            driver.set_battery_params(
                range=6,
                level=1,  # discharge current
                v_stop=3.1,
                v_on=2.5,
                v_enab_stop="ON",
            )

            driver.output_enable(True, channel=ch)

            # Track discharge for 2 mins
            voltage_measures = []
            for t in range(120):
                time.sleep(1)
                voltage = _finite(driver._query_checked_float("MEAS:VOLT?"), "battery voltage")
                voltage_measures.append((t, voltage))
                print(f"t={t:5.1f}s, V={voltage:.3f}")

            # Save voltage measurements over course of test
            with open("discharge_log.csv", "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["elapsed_time_s", "voltage_V"])
                writer.writerows(voltage_measures)

            driver.output_enable(False, channel=ch)

        if DISCHARGE_BATTERY:
            _run("battery mode: configure + discharge check", battery_discharge, failures)

    finally:
        try:
            eload.output_enable(False, channel=ch)
        except Exception:
            pass
        eload.close()
    return failures


@pytest.mark.hardware
def test_rigol_dl30xx_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED {len(failures)}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
