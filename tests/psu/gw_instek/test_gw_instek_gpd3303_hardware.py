"""Hardware validation for the GW Instek GPD-3303D via InstroPSU. Self-contained; no publishers.

Wiring / stimulus:
    Nothing connected to any output terminal (open circuit). CH1 and CH2 are exercised; CH3 is the
    fixed output and is only checked to be rejected. With no load, an enabled channel regulates in
    constant-voltage mode and reads about 0 A.

Run:
    uv run python tests/psu/gw_instek/test_gw_instek_gpd3303_hardware.py

Leaves the supply with its output off and both channels set to 0 V / 0 A.
"""

import sys
import time
from collections.abc import Callable

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig
from instro.psu import InstroPSU
from instro.psu.drivers import GWInstekGPD3303

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Find the port with `ls /dev/cu.*` (macOS) or the COM port in Device Manager (Windows); serial is 9600 8N1.
# NI-VISA numbers serial ports (ASRL1::INSTR); pyvisa-py names them by device path, so pin the backend with the path.
RESOURCE = "ASRL/dev/cu.usbserial-AQ02VCSL::INSTR"
VISA_BACKEND: str | None = "@py"
CHANNELS = (1, 2)
PROGRAMMED_VOLTAGE = 2.0
PROGRAMMED_CURRENT_LIMIT = 0.10
MAX_SAFE_VOLTAGE = 12.0
MAX_SAFE_CURRENT = 1.0
VOLTAGE_TOLERANCE = 0.25  # meter resolution is 0.1 V; allow a few counts of regulation error
CURRENT_TOLERANCE = 0.05
OUT_OF_RANGE_VOLTAGE = 40.0  # above the 32 V full scale; the device must reject it
OUTPUT_SETTLE_SECONDS = 1.0
BURST_REPEATS = 25

assert PROGRAMMED_VOLTAGE <= MAX_SAFE_VOLTAGE and PROGRAMMED_CURRENT_LIMIT <= MAX_SAFE_CURRENT


def _make_psu() -> InstroPSU:
    psu = InstroPSU(
        name="hw_validate",
        driver=GWInstekGPD3303(VisaConfig(RESOURCE, visa_backend=VISA_BACKEND)),
        num_channels=2,
        publishers=None,
    )
    psu.open()
    return psu


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _expect_close(actual: float, expected: float, tolerance: float, what: str) -> None:
    _expect(abs(actual - expected) <= tolerance, f"{what}: expected {expected} +/- {tolerance}, got {actual}")


def _run(name: str, fn: Callable[[], None], failures: list[tuple[str, Exception]]) -> None:
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def _check_setpoint_roundtrip(psu: InstroPSU, channel: int) -> None:
    psu.set_voltage(PROGRAMMED_VOLTAGE, channel)
    psu.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel)
    voltage = psu.get_voltage_setpoint(channel)
    current = psu.get_current_setpoint(channel)
    assert voltage is not None and current is not None
    _expect_close(float(voltage.latest), PROGRAMMED_VOLTAGE, 0.05, f"CH{channel} voltage setpoint")
    _expect_close(float(current.latest), PROGRAMMED_CURRENT_LIMIT, 0.005, f"CH{channel} current setpoint")


def _check_setpoint_resolution(psu: InstroPSU) -> None:
    psu.set_voltage(3.37, 1)
    psu.set_current_limit(0.123, 1)
    voltage = psu.get_voltage_setpoint(1)
    current = psu.get_current_setpoint(1)
    assert voltage is not None and current is not None
    _expect_close(float(voltage.latest), 3.4, 0.01, "CH1 voltage setpoint rounded to 0.1 V")
    _expect_close(float(current.latest), 0.12, 0.001, "CH1 current setpoint rounded to 10 mA")


def _check_out_of_range_rejected(psu: InstroPSU) -> None:
    psu.set_voltage(PROGRAMMED_VOLTAGE, 1)
    try:
        psu.set_voltage(OUT_OF_RANGE_VOLTAGE, 1)
    except RuntimeError as exc:
        _expect("out of range" in str(exc).lower(), f"unexpected error text: {exc}")
    else:
        raise AssertionError("out-of-range voltage was accepted without an error")
    after = psu.get_voltage_setpoint(1)
    assert after is not None
    _expect_close(float(after.latest), PROGRAMMED_VOLTAGE, 0.05, "CH1 setpoint after rejected write")
    psu.set_voltage(PROGRAMMED_VOLTAGE, 1)


def _check_back_to_back_writes(psu: InstroPSU) -> None:
    for i in range(BURST_REPEATS):
        voltage = PROGRAMMED_VOLTAGE + 0.1 * (i % 5)
        psu.set_voltage(voltage, 1)
        current = PROGRAMMED_CURRENT_LIMIT + 0.01 * (i % 5)
        psu.set_current_limit(current, 1)
        voltage_readback = psu.get_voltage_setpoint(1)
        current_readback = psu.get_current_setpoint(1)
        assert voltage_readback is not None and current_readback is not None
        _expect_close(float(voltage_readback.latest), voltage, 0.05, f"burst {i} voltage setpoint")
        _expect_close(float(current_readback.latest), current, 0.005, f"burst {i} current setpoint")
    for i in range(BURST_REPEATS):
        try:
            psu.set_voltage(OUT_OF_RANGE_VOLTAGE, 1)
        except RuntimeError:
            continue
        raise AssertionError(f"out-of-range write {i} in a burst was not reported")
    psu.set_voltage(PROGRAMMED_VOLTAGE, 1)


def _check_channel_validation(psu: InstroPSU) -> None:
    try:
        psu.set_voltage(1.0, 3)
    except FeatureNotSupportedError:
        pass
    else:
        raise AssertionError("channel 3 write did not raise FeatureNotSupportedError")
    try:
        psu.get_voltage(4)
    except ValueError:
        pass
    else:
        raise AssertionError("channel 4 read did not raise ValueError")


def _check_output_off_state(psu: InstroPSU) -> None:
    psu.output_enable(False, 1)
    for channel in CHANNELS:
        status = psu.get_output_status(channel)
        mode = psu.get_operating_mode(channel)
        assert status is not None and mode is not None
        _expect(not bool(status.latest), f"CH{channel} reports output on after disable")
        _expect(mode.latest == "OFF", f"CH{channel} mode with output off: {mode.latest}")


def _check_output_on_state(psu: InstroPSU) -> None:
    for channel in CHANNELS:
        psu.set_voltage(PROGRAMMED_VOLTAGE, channel)
        psu.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel)
    psu.output_enable(True, 1)
    time.sleep(OUTPUT_SETTLE_SECONDS)
    for channel in CHANNELS:
        status = psu.get_output_status(channel)
        voltage = psu.get_voltage(channel)
        current = psu.get_current(channel)
        mode = psu.get_operating_mode(channel)
        assert status is not None and voltage is not None and current is not None and mode is not None
        _expect(bool(status.latest), f"CH{channel} reports output off after enable")
        _expect_close(float(voltage.latest), PROGRAMMED_VOLTAGE, VOLTAGE_TOLERANCE, f"CH{channel} output voltage")
        _expect_close(float(current.latest), 0.0, CURRENT_TOLERANCE, f"CH{channel} output current (open circuit)")
        _expect(mode.latest == "CV", f"CH{channel} mode into open circuit: {mode.latest}")


def _check_status_stream_stays_in_sync(psu: InstroPSU) -> None:
    for _ in range(5):
        psu.get_output_status(1)
    voltage = psu.get_voltage(1)
    assert voltage is not None
    _expect(float(voltage.latest) >= 0.0, "voltage read after repeated STATUS? queries was not a clean number")


def _check_protection_methods_unsupported(psu: InstroPSU) -> None:
    calls: list[Callable[[], object]] = [
        lambda: psu.set_overvoltage_protection_level(10.0, 1),
        lambda: psu.get_overvoltage_protection_level(1),
        lambda: psu.set_overvoltage_protection_enabled(True, 1),
        lambda: psu.get_overvoltage_protection_enabled(1),
        lambda: psu.set_overvoltage_protection_delay(0.1, 1),
        lambda: psu.get_overvoltage_protection_delay(1),
        lambda: psu.set_overcurrent_protection_level(0.5, 1),
        lambda: psu.get_overcurrent_protection_level(1),
        lambda: psu.set_overcurrent_protection_enabled(True, 1),
        lambda: psu.get_overcurrent_protection_enabled(1),
        lambda: psu.clear_protection(1),
        lambda: psu.get_overvoltage_protection_tripped(1),
        lambda: psu.get_overcurrent_protection_tripped(1),
        lambda: psu.set_remote_sense_enabled(True, 1),
        lambda: psu.get_remote_sense_enabled(1),
    ]
    for call in calls:
        try:
            call()
        except FeatureNotSupportedError:
            continue
        raise AssertionError("a protection or remote-sense method did not raise FeatureNotSupportedError")


def _restore_safe_state(psu: InstroPSU) -> None:
    """Output off, then 0 V / 0 A on each channel; every step runs even if an earlier one fails."""
    steps: list[Callable[[], object]] = [lambda: psu.output_enable(False, 1)]
    for channel in CHANNELS:
        steps.append(lambda c=channel: psu.set_voltage(0.0, c))
        steps.append(lambda c=channel: psu.set_current_limit(0.0, c))
    try:
        for step in steps:
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - keep restoring the remaining steps
                print(f"  [WARN] safe-state step failed: {exc}")
    finally:
        psu.close()


def run_all() -> list[tuple[str, Exception]]:
    psu = _make_psu()
    failures: list[tuple[str, Exception]] = []
    try:
        for channel in CHANNELS:
            _run(f"CH{channel} setpoint roundtrip", lambda c=channel: _check_setpoint_roundtrip(psu, c), failures)
        _run("CH1 setpoint resolution (0.1 V / 10 mA)", lambda: _check_setpoint_resolution(psu), failures)
        _run("out-of-range voltage raises and is not applied", lambda: _check_out_of_range_rejected(psu), failures)
        _run(
            "back-to-back writes: roundtrips and errors still reported",
            lambda: _check_back_to_back_writes(psu),
            failures,
        )
        _run("channel 3 unsupported, channel 4 invalid", lambda: _check_channel_validation(psu), failures)
        _run("output off: status and operating mode", lambda: _check_output_off_state(psu), failures)
        _run("output on: status, readback, CV mode", lambda: _check_output_on_state(psu), failures)
        _run("repeated STATUS? keeps replies in sync", lambda: _check_status_stream_stays_in_sync(psu), failures)
        _run("protection and remote sense unsupported", lambda: _check_protection_methods_unsupported(psu), failures)
    finally:
        _restore_safe_state(psu)
    return failures


@pytest.mark.hardware
def test_gpd3303_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
