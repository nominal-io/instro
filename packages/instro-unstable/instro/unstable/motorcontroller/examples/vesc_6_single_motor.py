"""Example: single VESC 6 (VESC ID 0) on a CAN bus via a candleLight/gs_usb USB-CAN adapter.

Wire CAN_H/CAN_L (and GND) from the adapter to the VESC's COMM port; 120 ohm
termination at both ends of the bus. Other VESCs may share the bus; frames are
filtered to CONTROLLER_ID.

VESC Tool prerequisites: VESC ID = 0, CAN baud 500 kbps, CAN status message
mode including statuses 1/4/5 (broadcast telemetry doubles as the liveness
check), and FOC motor detection completed before commanding motion.

Run:
    uv run python packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6_single_motor.py
"""

import time

from instro.unstable.motorcontroller import InstroMotorController
from instro.unstable.motorcontroller.drivers import VESC6

CONTROLLER_ID = 0  # VESC Tool: App Settings -> General -> VESC ID
POLE_PAIRS = 4  # motor pole-pair count; wrong values scale set_velocity and velocity telemetry
TARGET_RPM = 1000.0  # keep RPM x POLE_PAIRS above the VESC's ~900 minimum regulated ERPM
RUN_SECONDS = 10.0

motor = InstroMotorController(
    "drive", driver=VESC6(channel=0, pole_pairs=POLE_PAIRS, controller_id=CONTROLLER_ID, interface="gs_usb")
)
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
