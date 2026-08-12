"""Example: single VESC 6 (VESC ID 0) over a Deshide SH-C31G USB-CAN adapter.

The SH-C31G is a CANable 2.0 derivative shipping candleLight firmware (gs_usb).
Wire CAN_H/CAN_L (and GND) to the VESC's COMM port; 120 ohm termination at both
ends. VESC Tool prerequisites: VESC ID = 0, CAN baud 500 kbps, CAN status
message mode 1-5, and FOC motor detection completed before commanding motion.

Windows host setup (python-can's gs_usb backend, not project deps):
    uv pip install gs-usb libusb-package

Run:
    uv run --no-sync python packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6_sh_c31g.py
"""

import time

import libusb_package
import usb.backend.libusb1

from instro.unstable.motorcontroller import InstroMotorController
from instro.unstable.motorcontroller.drivers import VESC6

CONTROLLER_ID = 0  # VESC Tool: App Settings -> General -> VESC ID
POLE_PAIRS = 6  # motor pole-pair count; wrong values scale set_velocity and velocity telemetry
TARGET_RPM = 300.0  # slow mechanical speed; keep RPM x POLE_PAIRS above the VESC's ~900 minimum regulated ERPM
RUN_SECONDS = 5.0

# python-can's gs_usb backend needs a libusb-1.0 DLL; libusb-package provides it.
usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)

motor = InstroMotorController("drive", driver=VESC6(channel=0, pole_pairs=POLE_PAIRS, controller_id=CONTROLLER_ID))
telemetry: dict[str, float] = {}


def poll() -> dict[str, float]:
    """Drain broadcast frames and merge the latest values (keys without the 'drive.' prefix)."""
    measurement = motor.get_telemetry()
    if measurement is not None:
        telemetry.update({key.removeprefix("drive."): values[-1] for key, values in measurement.channel_data.items()})
    return telemetry


motor.open()
try:
    deadline = time.monotonic() + 2.0
    while not poll():
        if time.monotonic() > deadline:
            raise SystemExit("no telemetry; check wiring, termination, bitrate, VESC ID, and CAN status message mode")
        time.sleep(0.1)
    print(f"VESC {CONTROLLER_ID} alive: bus={telemetry.get('bus_voltage')} V")

    for i in range(int(RUN_SECONDS / 0.05)):
        motor.set_velocity(TARGET_RPM)  # re-sent at 20 Hz; the VESC releases the motor ~0.5 s after the last command
        poll()
        if i % 10 == 0:
            print(
                f"commanded={TARGET_RPM} rpm measured={telemetry.get('velocity')} rpm "
                f"motor_current={telemetry.get('motor_current')} A"
            )
        time.sleep(0.05)
finally:
    motor.close()  # safe-stops the motor (zero current) before shutting the bus down
