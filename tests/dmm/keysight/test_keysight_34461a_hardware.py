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
    uv run python tests/dmm/keysight/test_keysight_34461a_hardware.py
"""

from __future__ import annotations

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


def _check_function(hal: InstroDMM, function: MeasurementFunction, expected) -> None:
    hal.set_measurement_function(function)
    value = _read_value(hal)
    print(f"         {function.value} read -> {value:g}")
    if expected is not None and value != pytest.approx(expected, rel=VALUE_TOLERANCE):
        raise AssertionError(f"{function.value}: {value:g} not within {VALUE_TOLERANCE:.0%} of {expected:g}")


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


@pytest.mark.hardware
def test_keysight_34461a_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
