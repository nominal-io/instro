"""Hardware validation for the Keithley 2400 SourceMeter via InstroDMM. Self-contained; no publishers.

Exercises every method the ``Keithley2400`` (sense-only DMM) driver implements:
connection/``*IDN?``; the three supported measurement functions (DC voltage, DC
current, 2-wire resistance) via set + read; per-function NPLC (``set_aperture_nplc``);
per-function range set (manual + auto, source path for V/I and sense path for ohms);
the unsupported-capability guards (AC function, ``set_digits``, ``set_aperture_seconds``,
all raise NotImplementedError); and the real-hardware error-query path.

Safety:
    The 2400 is a SourceMeter. This driver sources 0 V / 0 A to sense passively and
    briefly enables the output during each ``:READ?``; ``close()`` issues ``:OUTP OFF``.
    The ``finally`` block always closes the instrument, leaving the output OFF.

Wiring / stimulus:
    FRONT terminals, OPEN (nothing connected). Measurement checks are therefore
    STRUCTURAL only: each read must parse to a finite float and raise no SCPI error.
    With open terminals, V/I read ~0 (output forced to 0) and 2-wire resistance reads a
    large but finite value (autorange-clamped, ~1e8 Ω on a real 2400 — not necessarily
    the canonical 9.9e37 overflow sentinel). To add strict value checks, wire a known
    stimulus (e.g. a resistor) and set the matching ``EXPECTED_*``.

Run:
    uv run python tests/dmm/keithley/test_keithley_2400_hardware.py
"""

from __future__ import annotations

import math
import sys
import time

import pytest

from instro.dmm import InstroDMM, MeasurementFunction
from instro.dmm.drivers import Keithley2400

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
VISA_RESOURCE = "ASRL5::INSTR"  # <-- edit to your serial port (COM5)

# Strict value checks. Leave None for open terminals (structural checks only).
# Set one to the known stimulus value (in the function's base units) to enable a
# tolerance-based assertion on that function's read.
EXPECTED_DC_VOLTAGE = None  # volts
EXPECTED_DC_CURRENT = None  # amperes
EXPECTED_TWO_WIRE_RESISTANCE = None  # ohms
VALUE_TOLERANCE = 0.05  # relative tolerance for any enabled strict check

# (function, expected-value constant, manual-range) for the supported-function sweep.
# Range is the source path for V/I and the sense path for resistance.
_FUNCTION_SWEEP = [
    (MeasurementFunction.DC_VOLTAGE, EXPECTED_DC_VOLTAGE, 10.0),
    (MeasurementFunction.DC_CURRENT, EXPECTED_DC_CURRENT, 0.1),
    (MeasurementFunction.TWO_WIRE_RESISTANCE, EXPECTED_TWO_WIRE_RESISTANCE, 1000.0),
]


def _make_hal() -> InstroDMM:
    hal = InstroDMM(
        name="hw_validate",
        driver=Keithley2400(VISA_RESOURCE),
        publishers=None,
    )
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


def run_all() -> list:
    hal = _make_hal()
    failures: list = []
    try:
        _run(
            "connection / *IDN?",
            lambda: print(f"         IDN -> {hal._driver._visa.query('*IDN?').strip()}"),
            failures,
        )

        for function, expected, _range in _FUNCTION_SWEEP:
            _run(
                f"set_measurement_function + read: {function.value}",
                lambda f=function, e=expected: _check_function(hal, f, e),
                failures,
            )

        def _nplc() -> None:
            for function, _expected, _range in _FUNCTION_SWEEP:
                hal.set_measurement_function(function)
                hal.set_aperture_nplc(1.0)
                _read_value(hal)

        _run("set_aperture_nplc (V/I/ohms) + read", _nplc, failures)

        def _range_sweep() -> None:
            for function, _expected, manual_range in _FUNCTION_SWEEP:
                hal.set_measurement_function(function)
                hal.set_range(manual_range)  # manual range (source path for V/I, sense path for ohms)
                _read_value(hal)
                hal.set_range(None)  # auto range
                _read_value(hal)

        _run("set_range manual + auto (V/I/ohms)", _range_sweep, failures)

        def _ac_function_unsupported() -> None:
            # The HAL calls the driver before recording state, so a rejected AC function
            # leaves _measurement_config untouched (no restore needed).
            try:
                hal.set_measurement_function(MeasurementFunction.AC_VOLTAGE)
            except NotImplementedError:
                return
            raise AssertionError("set_measurement_function(AC_VOLTAGE) should raise NotImplementedError")

        _run("AC function unsupported (NotImplementedError)", _ac_function_unsupported, failures)

        def _set_digits_unsupported() -> None:
            try:
                hal.set_digits(5)
            except NotImplementedError:
                return
            raise AssertionError("set_digits should raise NotImplementedError on the 2400")

        _run("set_digits unsupported (NotImplementedError)", _set_digits_unsupported, failures)

        def _set_aperture_seconds_unsupported() -> None:
            try:
                hal.set_aperture_seconds(0.1)
            except NotImplementedError:
                return
            raise AssertionError("set_aperture_seconds should raise NotImplementedError on the 2400")

        _run("set_aperture_seconds unsupported (NotImplementedError)", _set_aperture_seconds_unsupported, failures)

        def _error_path() -> None:
            hal._driver._visa.write("INSTRO:INVALID")
            time.sleep(0.5)  # let the 2400 queue the error before :SYST:ERR? at 9600 baud
            try:
                hal._driver._check_errors()
            except RuntimeError:
                return
            finally:
                hal._driver._visa.write("*CLS")
            raise AssertionError("_check_errors should raise after an invalid command")

        _run("error-query path raises on bad command", _error_path, failures)

        # Reported for transparency: the 2400 sense-only driver does not implement AC
        # measurement (measure_ac_voltage / measure_ac_current raise NotImplementedError)
        # or 4-wire resistance, so those reads are out of scope for this validation. The
        # AC path is covered above at the set_measurement_function guard.
        print("  [SKIP] measure_ac_voltage / measure_ac_current / 4-wire resistance: not implemented by Keithley2400")
    finally:
        hal.close()  # issues :OUTP OFF, then closes the transport
    return failures


@pytest.mark.hardware
def test_keithley_2400_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
