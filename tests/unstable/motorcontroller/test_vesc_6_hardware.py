"""Hardware validation for VESC 6 via InstroMotorController. Self-contained; no publishers.

Wiring / stimulus:
    Jhoinrch (CANable-derivative) USB-CAN adapter in candleLight/gs_usb mode on a
    500 kbps bus with one powered VESC 6 (VESC ID 102, CAN status broadcasts enabled).
    No motor attached unless MOTOR_ATTACHED is True; without a motor, motion
    commands are validated at the wire level only (frames sent, VESC stays alive).

Host setup (not project deps):
    uv pip install gs-usb libusb-package

Run:
    uv run --no-sync python tests/unstable/motorcontroller/test_vesc_6_hardware.py
"""

import math
import sys
import time

import pytest

from instro.unstable.motorcontroller import InstroMotorController
from instro.unstable.motorcontroller.drivers import VESC6

CHANNEL = 0  # gs_usb device index          <-- edit before running
CONTROLLER_ID = 102  # VESC Tool "VESC ID"        <-- edit before running
INTERFACE = "gs_usb"
BITRATE = 500_000
POLE_PAIRS = 7  # motor pole-pair count      <-- edit to match the motor
MOTOR_ATTACHED = False  # True enables motion-effect checks (motor must spin freely)
MAX_TEST_CURRENT_A = 2.0
TEST_DUTY = 0.05
TEST_RPM = 500.0
TELEMETRY_TIMEOUT_S = 3.0


def _ensure_libusb_backend() -> None:
    """python-can's gs_usb backend needs a libusb-1.0 DLL; on Windows libusb-package provides it."""
    try:
        import libusb_package
        import usb.backend.libusb1

        usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    except ImportError:
        pass


def _run(name, fn, failures: list) -> None:
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def _wait_for_telemetry(motor: InstroMotorController) -> dict[str, float]:
    deadline = time.monotonic() + TELEMETRY_TIMEOUT_S
    while time.monotonic() < deadline:
        measurement = motor.get_telemetry()
        if measurement is not None:
            return {k: v[0] for k, v in measurement.channel_data.items()}
        time.sleep(0.1)
    raise AssertionError(
        f"no broadcast telemetry within {TELEMETRY_TIMEOUT_S}s; "
        "check CAN status message mode, bitrate, wiring, and VESC ID"
    )


def _stream(motor: InstroMotorController, send, seconds: float) -> None:
    """Re-send a setpoint as keep-alive; the VESC releases the motor ~0.5 s after the last frame."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        send()
        time.sleep(0.05)


def run_all() -> list:
    _ensure_libusb_backend()
    driver = VESC6(
        channel=CHANNEL, pole_pairs=POLE_PAIRS, controller_id=CONTROLLER_ID, interface=INTERFACE, bitrate=BITRATE
    )
    motor = InstroMotorController("hw_validate", driver=driver, publishers=None)
    failures: list = []
    telemetry: dict[str, float] = {}

    motor.open()
    try:

        def check_telemetry_liveness():
            telemetry.update(_wait_for_telemetry(motor))

        _run("telemetry: broadcast frames arrive and parse", check_telemetry_liveness, failures)

        def check_telemetry_sanity():
            assert telemetry, "no telemetry captured"
            for key, value in telemetry.items():
                assert math.isfinite(value), f"{key} not finite: {value}"
            voltage = telemetry.get("hw_validate.bus_voltage")
            if voltage is not None:
                assert 5.0 <= voltage <= 70.0, f"bus_voltage implausible: {voltage} V"

        _run("telemetry: values finite, bus voltage plausible", check_telemetry_sanity, failures)

        amps = min(1.0, MAX_TEST_CURRENT_A)
        _run("set_current: frame accepted", lambda: motor.set_current(amps), failures)
        _run("stop_motor: frame accepted", lambda: motor.stop_motor(), failures)
        _run("set_duty_cycle: frame accepted", lambda: motor.set_duty_cycle(TEST_DUTY), failures)
        _run("set_velocity: frame accepted", lambda: motor.set_velocity(TEST_RPM), failures)
        _run("set_brake_current: frame accepted", lambda: motor.set_brake_current(1.0), failures)
        _run("set_position: frame accepted", lambda: motor.set_position(90.0), failures)
        _run("stop_motor: motor released", lambda: motor.stop_motor(), failures)

        if MOTOR_ATTACHED:

            def check_duty_spins_motor():
                _stream(motor, lambda: motor.set_duty_cycle(TEST_DUTY), seconds=1.5)
                spinning = _wait_for_telemetry(motor)
                motor.stop_motor()
                velocity = spinning.get("hw_validate.velocity")
                assert velocity is not None and abs(velocity) > 0, f"velocity did not respond: {velocity}"

            def check_stop_halts_motor():
                _stream(motor, lambda: motor.stop_motor(), seconds=1.0)
                time.sleep(0.5)
                stopped = _wait_for_telemetry(motor)
                velocity = stopped.get("hw_validate.velocity", 0.0)
                assert abs(velocity) < 50, f"velocity nonzero after stop: {velocity}"

            _run("motion: duty command spins motor (telemetry velocity != 0)", check_duty_spins_motor, failures)
            _run("motion: stop_motor halts motor", check_stop_halts_motor, failures)
        else:
            print("  [SKIP] motion-effect checks: MOTOR_ATTACHED is False (no motor on the bench)")

        def check_enable_unsupported():
            with pytest.raises(NotImplementedError):
                motor.enable_motor()
            with pytest.raises(NotImplementedError):
                motor.disable_motor()

        _run(
            "enable_motor/disable_motor: raise NotImplementedError (VESC has no explicit enable)",
            check_enable_unsupported,
            failures,
        )

        def check_telemetry_after_commands():
            after = _wait_for_telemetry(motor)
            assert after, "telemetry stopped after command sequence (VESC faulted or bus down)"

        _run("telemetry: still flowing after command sequence", check_telemetry_after_commands, failures)

        def check_daemon_roundtrip():
            motor.background_interval = 0.1
            motor.start()
            time.sleep(1.0)
            # velocity rides STATUS_1, which every broadcast mode includes; bus_voltage needs STATUS_5
            measurement = motor.get_channel("hw_validate.velocity", timeout=2.0)
            motor.stop()
            assert measurement is not None

        _run("daemon: background telemetry fills channel buffer, stop() joins", check_daemon_roundtrip, failures)
    finally:
        try:
            motor.stop_motor()
        except Exception:  # noqa: BLE001 - best-effort safe state
            pass
        motor.close()
    return failures


@pytest.mark.hardware
def test_vesc_6_hardware():
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
