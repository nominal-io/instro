"""Hardware validation for the Keysight 34461A via InstroDMM. Self-contained; no publishers.

Exercises every method the ``Keysight34461A`` driver implements: connection/``*IDN?``,
all six measurement functions (set + read), per-function range set (manual + auto),
per-function NPLC set (DC and resistance functions), the AC-NPLC and ``set_digits``
unsupported guards, and the real-hardware error-query path.

Wiring / stimulus:
    Inputs OPEN (nothing connected). All measurement checks are therefore STRUCTURAL
    only: each read must parse to a finite float and raise no SCPI error. With open
    inputs the 34461A returns small noise on voltage/current and its overload sentinel
    (~9.9e37, still a finite float) on resistance. To add strict value checks, wire a
    known stimulus and set the matching ``EXPECTED_*`` constant below.

Run:
    uv run python tests/dmm/keysight/test_keysight_34461a_hardware.py [--stage STAGE]

Stages (wired strict-value passes; wiring noted per stage):
    smoke  (default) full structural sweep, open inputs
    dc     PSU 5.000 V / 50 mA limit on rails; 1 kOhm loop into the 3A jack
    ac     LabJack T4 DAC0 sine 100 Hz, 2.5 V offset, 2.0 V amplitude on rails
    ohms2  DMM HI/LO on b15/b20 (10 kOhm reference)
    ohms4  DMM HI/LO on a25/a30, sense on b25/b30 (220 Ohm reference)
"""

from __future__ import annotations

import argparse
import math
import sys

import pytest

from instro.dmm import InstroDMM, MeasurementFunction
from instro.dmm.drivers import Keysight34461A

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
VISA_RESOURCE = "USB0::0x2A8D::0x1301::MY64031276::INSTR"  # <-- edit to your unit

# Strict value checks. Leave None for open inputs (structural checks only).
# Set one to the known stimulus value (in the function's base units) to enable a
# tolerance-based assertion on that function's read.
EXPECTED_DC_VOLTAGE = None  # volts
EXPECTED_AC_VOLTAGE = None  # volts RMS
EXPECTED_DC_CURRENT = None  # amperes
EXPECTED_AC_CURRENT = None  # amperes RMS
EXPECTED_TWO_WIRE_RESISTANCE = None  # ohms
EXPECTED_FOUR_WIRE_RESISTANCE = None  # ohms
VALUE_TOLERANCE = 0.05  # relative tolerance for any enabled strict check

# Wired-stage expectations; wiring is described in the module docstring stages.
STAGE_DC_VOLTAGE = 5.0  # V, bench PSU at 5.000 V
STAGE_DC_CURRENT = 5.0e-3  # A, 5 V through the 1 kOhm limiter into the 3A jack
STAGE_AC_VOLTAGE = 1.414  # V rms, 2.0 Vpk DAC0 sine (AC-coupled, offset invisible)
STAGE_TWO_WIRE_OHMS = 10_000.0  # 10 kOhm 5% reference
STAGE_FOUR_WIRE_OHMS = 220.0  # 220 Ohm 5% reference
STAGE_V_I_TOLERANCE = 0.05
STAGE_OHMS_TOLERANCE = 0.10  # 5% resistors, doubled headroom

# (function, expected value, valid manual range for the range sweep, NPLC support)
_FUNCTION_SWEEP = [
    (MeasurementFunction.DC_VOLTAGE, EXPECTED_DC_VOLTAGE, 10.0, True),
    (MeasurementFunction.AC_VOLTAGE, EXPECTED_AC_VOLTAGE, 10.0, False),
    (MeasurementFunction.DC_CURRENT, EXPECTED_DC_CURRENT, 1.0, True),
    (MeasurementFunction.AC_CURRENT, EXPECTED_AC_CURRENT, 1.0, False),
    (MeasurementFunction.TWO_WIRE_RESISTANCE, EXPECTED_TWO_WIRE_RESISTANCE, 10e3, True),
    (MeasurementFunction.FOUR_WIRE_RESISTANCE, EXPECTED_FOUR_WIRE_RESISTANCE, 10e3, True),
]

# 34461A-valid NPLC values; ends on the power-on default of 10.
_NPLC_VALUES = (0.02, 0.2, 1, 100, 10)


def _make_hal() -> InstroDMM:
    hal = InstroDMM(name="hw_validate", driver=Keysight34461A(VISA_RESOURCE), publishers=None)
    hal.open()
    return hal


def _run(name, fn, failures) -> None:
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def _read_value(hal: InstroDMM) -> float:
    """Read under the active function and assert the result is a finite float."""
    value = hal.read().latest
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError(f"expected a numeric reading, got {value!r}")
    fvalue = float(value)
    if not math.isfinite(fvalue):
        raise AssertionError(f"non-finite reading: {fvalue}")
    return fvalue


def _check_function(hal: InstroDMM, function: MeasurementFunction, expected, tolerance=VALUE_TOLERANCE) -> None:
    hal.set_measurement_function(function)
    value = _read_value(hal)
    print(f"         {function.value} read -> {value:g}")
    if expected is not None and value != pytest.approx(expected, rel=tolerance):
        raise AssertionError(f"{function.value}: {value:g} not within {tolerance:.0%} of {expected:g}")


def _check_range(hal: InstroDMM, function: MeasurementFunction, manual_range: float) -> None:
    hal.set_measurement_function(function)
    hal.set_range(manual_range)
    _read_value(hal)
    hal.set_range(None)
    _read_value(hal)


def _check_nplc(hal: InstroDMM, function: MeasurementFunction) -> None:
    hal.set_measurement_function(function)
    for nplc in _NPLC_VALUES:
        hal.set_aperture_nplc(nplc)
    _read_value(hal)


def run_all() -> list:
    hal = _make_hal()
    failures: list = []
    try:
        _run(
            "connection / *IDN?",
            lambda: print(f"         IDN -> {hal._driver._visa.query('*IDN?').strip()}"),
            failures,
        )

        for function, expected, _manual_range, _has_nplc in _FUNCTION_SWEEP:
            _run(
                f"set_measurement_function + read: {function.value}",
                lambda f=function, e=expected: _check_function(hal, f, e),
                failures,
            )

        for function, _expected, manual_range, _has_nplc in _FUNCTION_SWEEP:
            _run(
                f"set_range manual({manual_range:g}) + auto: {function.value}",
                lambda f=function, r=manual_range: _check_range(hal, f, r),
                failures,
            )

        for function, _expected, _manual_range, has_nplc in _FUNCTION_SWEEP:
            if not has_nplc:
                continue
            _run(
                f"set_aperture_nplc {_NPLC_VALUES}: {function.value}",
                lambda f=function: _check_nplc(hal, f),
                failures,
            )

        def _ac_nplc_guard() -> None:
            for function in (MeasurementFunction.AC_VOLTAGE, MeasurementFunction.AC_CURRENT):
                hal.set_measurement_function(function)
                try:
                    hal.set_aperture_nplc(1)
                except NotImplementedError:
                    continue
                raise AssertionError(f"set_aperture_nplc under {function.value} should raise NotImplementedError")

        _run("AC NPLC raises NotImplementedError (bandwidth-based)", _ac_nplc_guard, failures)

        def _digits_guard() -> None:
            hal.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
            try:
                hal.set_digits(6)
            except NotImplementedError:
                return
            raise AssertionError("set_digits should raise NotImplementedError on the 34461A")

        _run("set_digits raises NotImplementedError", _digits_guard, failures)

        def _error_path() -> None:
            hal._driver._visa.write("INSTRO:INVALID")
            try:
                hal._driver._check_errors()
            except RuntimeError:
                return
            finally:
                hal._driver._visa.write("*CLS")
            raise AssertionError("_check_errors should raise after an invalid command")

        _run("error-query path raises on bad command", _error_path, failures)

        # Reported for transparency: set_aperture_seconds is 34465A/70A-only and is
        # not implemented by Keysight34461A, so it is out of scope for this validation.
        print("  [SKIP] set_aperture_seconds: not implemented by Keysight34461A (34465A/70A only)")
    finally:
        hal.close()
    return failures


# Per-stage strict checks: (label, function, expected value or None for structural, tolerance).
_STAGE_CHECKS = {
    "dc": [
        ("measure_dc_voltage -> 5.0 V +/-5%", MeasurementFunction.DC_VOLTAGE, STAGE_DC_VOLTAGE, STAGE_V_I_TOLERANCE),
        ("measure_dc_current -> 5.0 mA +/-5%", MeasurementFunction.DC_CURRENT, STAGE_DC_CURRENT, STAGE_V_I_TOLERANCE),
    ],
    "ac": [
        (
            "measure_ac_voltage -> 1.414 V rms +/-5%",
            MeasurementFunction.AC_VOLTAGE,
            STAGE_AC_VOLTAGE,
            STAGE_V_I_TOLERANCE,
        ),
        ("measure_ac_current structural (no stimulus)", MeasurementFunction.AC_CURRENT, None, None),
    ],
    "ohms2": [
        (
            "measure_resistance (2-wire) -> 10 kOhm +/-10%",
            MeasurementFunction.TWO_WIRE_RESISTANCE,
            STAGE_TWO_WIRE_OHMS,
            STAGE_OHMS_TOLERANCE,
        ),
    ],
    "ohms4": [
        (
            "measure_four_wire_resistance -> 220 Ohm +/-10%",
            MeasurementFunction.FOUR_WIRE_RESISTANCE,
            STAGE_FOUR_WIRE_OHMS,
            STAGE_OHMS_TOLERANCE,
        ),
    ],
}


def _restore_safe_state(hal: InstroDMM) -> None:
    hal.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    hal.set_range(None)


def run_stage(stage: str) -> list:
    hal = _make_hal()
    failures: list = []
    print(f"Stage '{stage}':")
    try:
        for label, function, expected, tolerance in _STAGE_CHECKS[stage]:
            _run(
                label,
                lambda f=function, e=expected, t=tolerance: _check_function(hal, f, e, t or VALUE_TOLERANCE),
                failures,
            )
        _run("restore safe state (DCV, autorange)", lambda: _restore_safe_state(hal), failures)
    finally:
        hal.close()
    return failures


@pytest.mark.hardware
def test_keysight_34461a_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Keysight 34461A hardware validation")
    parser.add_argument("--stage", choices=("smoke", *_STAGE_CHECKS), default="smoke")
    args = parser.parse_args()
    failures = run_all() if args.stage == "smoke" else run_stage(args.stage)
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
