"""Hardware validation for VESC 6 via InstroMotorController. Self-contained; no publishers.

Wiring / stimulus:
    Jhoinrch (CANable-derivative) USB-CAN adapter in candleLight/gs_usb mode on a
    500 kbps bus with one powered VESC 6 (VESC ID 1, CAN status mode 1-5 enabled).
    No motor attached unless MOTOR_ATTACHED is True; without a motor, motion
    commands are validated at the wire level only (frames sent, VESC stays alive).

Run:
    uv run python tests/unstable/motorcontroller/test_vesc_6_hardware.py
"""

import math
import sys
import time

import pytest

from instro.unstable.motorcontroller import InstroMotorController
from instro.unstable.motorcontroller.drivers import VESC6

CHANNEL = 0  # gs_usb device index          <-- edit before running
CONTROLLER_ID = 1  # VESC Tool "VESC ID"        <-- edit before running
INTERFACE = "gs_usb"
BITRATE = 500_000
POLE_PAIRS = 4  # motor pole-pair count      <-- edit to match the motor
MOTOR_ATTACHED = False  # True enables motion-effect checks (motor must spin freely)
MAX_TEST_CURRENT_A = 2.0
TEST_DUTY = 0.05
TEST_RPM = 500.0
TELEMETRY_TIMEOUT_S = 3.0
EXPECT_BUS_VOLTAGE = True  # requires CAN status mode 1-5 (STATUS_5 carries bus voltage)
EXPECTED_BUS_VOLTAGE_V = None  # set to the supply voltage to enable the strict value check


def _run(name, fn, failures: list) -> None:
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def _wait_for_telemetry(motor: InstroMotorController) -> dict[str, float]:
    """Merge drains until telemetry appears, then settle 0.5 s so every frame in the broadcast round-robin lands."""
    merged: dict[str, float] = {}
    deadline = time.monotonic() + TELEMETRY_TIMEOUT_S
    settle_until: float | None = None
    while time.monotonic() < deadline:
        measurement = motor.get_telemetry()
        if measurement is not None:
            merged.update({k: v[0] for k, v in measurement.channel_data.items()})
            if settle_until is None:
                settle_until = time.monotonic() + 0.5
        if settle_until is not None and time.monotonic() >= settle_until:
            return merged
        time.sleep(0.05)
    if merged:
        return merged
    raise AssertionError(
        f"no broadcast telemetry within {TELEMETRY_TIMEOUT_S}s; "
        "check CAN status message mode, bitrate, wiring, and VESC ID"
    )


def _stream(motor: InstroMotorController, send, seconds: float) -> dict[str, float]:
    """Re-send a setpoint as keep-alive, draining telemetry as we go; a full gs_usb RX FIFO drops the NEWEST frames.

    Returns the latest telemetry snapshot observed while streaming.
    """
    latest: dict[str, float] = {}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        send()
        measurement = motor.get_telemetry()
        if measurement is not None:
            latest.update({k: v[0] for k, v in measurement.channel_data.items()})
        time.sleep(0.05)
    return latest


def run_all() -> list:
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
            if EXPECT_BUS_VOLTAGE:
                assert voltage is not None, "bus_voltage absent; is CAN status mode 1-5 written to the VESC?"
            if voltage is not None:
                assert 5.0 <= voltage <= 70.0, f"bus_voltage implausible: {voltage} V"
            if voltage is not None and EXPECTED_BUS_VOLTAGE_V is not None:
                assert abs(voltage - EXPECTED_BUS_VOLTAGE_V) <= 0.05 * EXPECTED_BUS_VOLTAGE_V, (
                    f"bus_voltage {voltage} V not within 5% of expected {EXPECTED_BUS_VOLTAGE_V} V"
                )

        _run("telemetry: values finite, bus voltage present and plausible", check_telemetry_sanity, failures)

        amps = min(1.0, MAX_TEST_CURRENT_A)
        _run("set_current: frame accepted", lambda: motor.set_current(amps), failures)
        _run("stop_motor: frame accepted", lambda: motor.stop_motor(), failures)
        _run("set_duty_cycle: frame accepted", lambda: motor.set_duty_cycle(TEST_DUTY), failures)
        _run("set_velocity: frame accepted", lambda: motor.set_velocity(TEST_RPM), failures)
        _run("set_brake_current: frame accepted", lambda: motor.set_brake_current(1.0), failures)
        _run("set_position: frame accepted", lambda: motor.set_position(90.0), failures)
        _run("stop_motor: motor released", lambda: motor.stop_motor(), failures)

        def check_duty_echo():
            """Motorless closed-loop check: the VESC reports commanded duty in STATUS_1 even unloaded."""
            streaming = _stream(motor, lambda: motor.set_duty_cycle(TEST_DUTY), seconds=1.2)
            motor.stop_motor()
            echoed = streaming.get("hw_validate.duty_cycle")
            assert echoed is not None, "no duty_cycle in telemetry"
            assert abs(echoed - TEST_DUTY) <= 0.02, f"duty echo {echoed} != commanded {TEST_DUTY}"

        _run("duty echo: commanded duty appears in telemetry", check_duty_echo, failures)

        def check_watchdog_decay():
            """Stop streaming and confirm the ~0.5 s firmware timeout releases the motor (duty back to 0)."""
            streaming = _stream(motor, lambda: motor.set_duty_cycle(TEST_DUTY), seconds=1.0)
            assert streaming.get("hw_validate.duty_cycle"), "duty never applied; decay check would be vacuous"
            duty = None
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                measurement = motor.get_telemetry()
                if measurement is not None and "hw_validate.duty_cycle" in measurement.channel_data:
                    duty = measurement.channel_data["hw_validate.duty_cycle"][0]
                    if abs(duty) <= 0.01:
                        return
                time.sleep(0.1)
            raise AssertionError(f"duty did not decay to 0 within 2.5s of last command (last seen: {duty})")

        _run("watchdog: duty decays to 0 after commands cease", check_watchdog_decay, failures)

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
