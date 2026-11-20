"""Hardware validation for the Fluke 8845A via InstroDMM. Self-contained; no publishers.

Exercises every method the ``Fluke8845A`` driver implements: connection/``*IDN?``, all six
measurement functions (set + read), ``set_digits`` (4/5/6 and the invalid-digit guard), manual
and auto range, a large manual range (``1e+08``), and the error-query path.

Meter setup (front panel): ``8845`` command set; over RS-232, ``COMPUTER`` mode, 9600 baud,
no parity, 8 data bits, no flow control, EOL ``CR LF``.

Wiring / stimulus:
    Any input state. Measurement checks are STRUCTURAL only: each read must parse to a finite
    float and raise no SCPI error. To add strict value checks, wire a known stimulus and set the
    matching ``EXPECTED_*`` constant below.

Run:
    uv run --no-dev --group test --extra unstable python tests/unstable/dmm/test_fluke_8845a_hardware.py
"""

from __future__ import annotations

import collections
import math
import sys
import time

import pytest

from instro.dmm import InstroDMM, MeasurementFunction
from instro.lib.transports import SerialConfig, VisaConfig
from instro.unstable.dmm.drivers import Fluke8845A

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
VISA_RESOURCE = "ASRL6::INSTR"  # <-- edit to your serial port
BAUD_RATE = 9600  # <-- match the 8845A's front-panel RS-232 baud setting
VISA_BACKEND = "@py"  # <-- pyvisa backend; "@py" for pyvisa-py, None for system IVI/NI-VISA

# Strict value checks. Leave None for structural checks only. Set one to the known stimulus
# value (in the function's base units) to enable a tolerance-based assertion on that read.
EXPECTED_DC_VOLTAGE = None  # volts
EXPECTED_AC_VOLTAGE = None  # volts RMS
EXPECTED_DC_CURRENT = None  # amperes
EXPECTED_AC_CURRENT = None  # amperes RMS
EXPECTED_TWO_WIRE_RESISTANCE = None  # ohms
EXPECTED_FOUR_WIRE_RESISTANCE = None  # ohms
VALUE_TOLERANCE = 0.05  # relative tolerance for any enabled strict check

# Repeats of the 5½/6½-digit reads, which intermittently timed out before short-form numbers.
DIGITS_REPEATS = 10

_FUNCTION_SWEEP = [
    (MeasurementFunction.DC_VOLTAGE, EXPECTED_DC_VOLTAGE),
    (MeasurementFunction.AC_VOLTAGE, EXPECTED_AC_VOLTAGE),
    (MeasurementFunction.DC_CURRENT, EXPECTED_DC_CURRENT),
    (MeasurementFunction.AC_CURRENT, EXPECTED_AC_CURRENT),
    (MeasurementFunction.TWO_WIRE_RESISTANCE, EXPECTED_TWO_WIRE_RESISTANCE),
    (MeasurementFunction.FOUR_WIRE_RESISTANCE, EXPECTED_FOUR_WIRE_RESISTANCE),
]


def _make_hal() -> InstroDMM:
    hal = InstroDMM(
        name="hw_validate",
        driver=Fluke8845A(
            VisaConfig(
                visa_resource=VISA_RESOURCE,
                visa_backend=VISA_BACKEND,
                serial_config=SerialConfig(baud_rate=BAUD_RATE),
            )
        ),
        publishers=None,
    )
    visa = hal._driver._visa
    send_query = visa.query

    def traced_query(command: str) -> str:
        _recent_queries.append(command)
        return send_query(command)

    visa.query = traced_query  # the 8845A fails intermittently; name the query that hung
    hal.open()
    return hal


_recent_queries: collections.deque[str] = collections.deque(maxlen=3)


def _run(name, fn, failures) -> None:
    _recent_queries.clear()
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        print(f"         last queries: {list(_recent_queries)}")
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

        for function, expected in _FUNCTION_SWEEP:
            _run(
                f"set_measurement_function + read: {function.value}",
                lambda f=function, e=expected: _check_function(hal, f, e),
                failures,
            )

        # Before _digits: the driver's resolution cache, once set, would ride along on this range.
        def _large_range() -> None:
            hal.set_measurement_function(MeasurementFunction.TWO_WIRE_RESISTANCE)
            hal.set_range(1e8)  # sent as "1e+08"
            try:
                print(f"         RESISTANCE @ 100 MOhm range -> {_read_value(hal):g}")
            finally:
                # The range cache is shared across functions; leaving 1e8 set would break later steps.
                hal.set_range(None)
                hal._driver._visa.write("*CLS")

        _run("set_range 100 MOhm (2-wire resistance)", _large_range, failures)

        def _digits() -> None:
            hal.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
            # Resolution only reaches the wire alongside a range, so set one first.
            hal.set_range(10.0)
            for _ in range(DIGITS_REPEATS):
                for n in (4, 5, 6):
                    hal.set_digits(n)
                    _read_value(hal)
            try:
                hal.set_digits(3)
            except ValueError:
                pass
            else:
                raise AssertionError("set_digits(3) should raise ValueError")

        _run(f"set_digits 4/5/6 x{DIGITS_REPEATS} + invalid-digit guard", _digits, failures)

        def _range() -> None:
            hal.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
            hal.set_range(10.0)  # manual range
            _read_value(hal)
            hal.set_range(None)  # auto range
            _read_value(hal)

        _run("set_range manual + auto (DC voltage)", _range, failures)

        def _error_path() -> None:
            hal._driver._visa.write("INSTRO:INVALID")
            time.sleep(0.5)  # let the meter queue the error before SYST:ERR? at 9600 baud
            try:
                hal._driver._check_errors()
            except RuntimeError as exc:
                if not str(exc).startswith("Fluke 8845A reported error"):
                    raise AssertionError(f"unexpected error text: {exc}") from exc
                return
            finally:
                hal._driver._visa.write("*CLS")
            raise AssertionError("_check_errors should raise after an invalid command")

        _run("error-query path raises on bad command", _error_path, failures)

        print("  [SKIP] set_aperture_nplc / set_aperture_seconds: not implemented by Fluke8845A")
    finally:
        hal.close()
    return failures


@pytest.mark.hardware
def test_fluke_8845a_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
