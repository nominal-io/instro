"""Example: single VESC 6 (VESC ID 0) over a Deshide SH-C31G USB-CAN adapter.

The SH-C31G is a CANable 2.0 derivative (isolated, screw-terminal) shipping
candleLight firmware, so python-can talks to it via gs_usb. Wire CAN_H/CAN_L
(and GND) from the terminal block to the VESC's COMM port; 120 ohm termination
at both ends of the bus.

Windows host setup (python-can's gs_usb backend, not project deps):
    uv pip install gs-usb libusb-package

Run:
    uv run --no-sync python packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6_sh_c31g.py

VESC Tool prerequisites: VESC ID = 0, CAN baud 500 kbps, CAN status message
mode 1-5 (broadcast telemetry doubles as the liveness check), and FOC motor
detection completed before commanding any motion.
"""

import time

from instro.unstable.motorcontroller import InstroMotorController
from instro.unstable.motorcontroller.drivers import VESC6

CONTROLLER_ID = 0  # VESC Tool: App Settings -> General -> VESC ID
POLE_PAIRS = 7  # motor pole-pair count; wrong values scale set_velocity and velocity telemetry
DUTY = 0.05  # gentle 5% duty; observable in telemetry even with no motor attached
RUN_SECONDS = 5.0


def _ensure_libusb_backend() -> None:
    """python-can's gs_usb backend needs a libusb-1.0 DLL; on Windows libusb-package provides it."""
    try:
        import libusb_package
        import usb.backend.libusb1

        usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    except ImportError:
        pass


_ensure_libusb_backend()

driver = VESC6(channel=0, pole_pairs=POLE_PAIRS, controller_id=CONTROLLER_ID, interface="gs_usb")
motor = InstroMotorController("drive", driver=driver)
motor.background_interval = 0.1  # drain broadcast telemetry at 10 Hz (slow drains overflow the adapter FIFO)
motor.open()


def latest(field: str) -> float | None:
    try:
        measurement = motor.get_channel(f"{motor.name}.{field}", timeout=0)
        return next(iter(measurement.channel_data.values()))[-1]
    except Exception:
        return None  # field not broadcast (or no frame seen) yet


try:
    # Launches the background daemon: get_telemetry() runs every background_interval,
    # publishing velocity/current/duty/temps/voltage and filling the channel buffer.
    motor.start()

    # Liveness check: broadcast telemetry doubles as the connectivity test.
    deadline = time.monotonic() + 2.0
    while latest("velocity") is None:
        if time.monotonic() > deadline:
            raise SystemExit(
                "no broadcast telemetry seen; check wiring, termination, bitrate, VESC ID, and CAN status message mode"
            )
        time.sleep(0.1)
    print(f"VESC {CONTROLLER_ID} alive: bus={latest('bus_voltage')} V fet_temp={latest('fet_temperature')} C")

    deadline = time.monotonic() + RUN_SECONDS
    last_print = 0.0
    while time.monotonic() < deadline:
        motor.set_duty_cycle(DUTY)  # re-sent at 20 Hz; the VESC releases the motor ~0.5 s after the last command
        time.sleep(0.05)
        if time.monotonic() - last_print >= 0.5:
            last_print = time.monotonic()
            print(
                f"duty={latest('duty_cycle')} velocity={latest('velocity')} rpm "
                f"motor_current={latest('motor_current')} A v_bus={latest('bus_voltage')} V"
            )

    motor.stop_motor()
    print(f"Stopped. Final velocity={latest('velocity')} rpm")

finally:
    motor.close()
