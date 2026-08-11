"""Example: VESC 6 over CAN with a Canable 2.0 Pro, behind InstroMotorController.

Spins the motor with a current setpoint while the background daemon drains the
VESC's broadcast status frames into the channel buffer (and any publishers).

Two things the daemon does NOT do:
- It does not keep the motor alive. The VESC releases the motor ~0.5 s after
  the last command frame, so the foreground loop re-sends the setpoint.
- It does not enable the broadcasts. Turn them on in VESC Tool under
  App Settings -> General -> "CAN status message mode" (and rate).
"""

import time

from instro.unstable.motorcontroller import InstroMotorController
from instro.unstable.motorcontroller.drivers import VESC6

CONTROLLER_ID = 0  # VESC Tool: App Settings -> General -> VESC ID
POLE_PAIRS = 7  # motor pole pairs, for mechanical-RPM velocity telemetry
RUN_SECONDS = 5.0
MOTOR_CURRENT_A = 2.0

# Canable 2.0 Pro with stock candleLight firmware:
driver = VESC6(channel=0, controller_id=CONTROLLER_ID, interface="gs_usb", pole_pairs=POLE_PAIRS)
# ...or with slcan firmware flashed (adjust the COM port):
# driver = VESC6(channel="COM4", controller_id=CONTROLLER_ID, interface="slcan", pole_pairs=POLE_PAIRS)

motor = InstroMotorController("drive", driver=driver)
motor.background_interval = 0.1  # drain broadcast telemetry at 10 Hz
motor.open()


def latest(field: str) -> float | None:
    try:
        measurement = motor.get_channel(f"{motor.name}.{field}", timeout=0)
        return next(iter(measurement.channel_data.values()))[-1]
    except Exception:
        return None  # field not broadcast (or no frame seen) yet


try:
    if not driver.ping():
        raise SystemExit("VESC did not answer PING; check wiring, bitrate, and controller ID")

    # Launches the background daemon: get_telemetry() runs every
    # background_interval, publishing velocity/current/duty/temps/voltage and
    # filling the channel buffer read by get_channel().
    motor.start()

    deadline = time.monotonic() + RUN_SECONDS
    last_print = 0.0
    while time.monotonic() < deadline:
        motor.set_current(MOTOR_CURRENT_A)  # re-sent at 20 Hz as keep-alive
        time.sleep(0.05)
        if time.monotonic() - last_print >= 0.5:
            last_print = time.monotonic()
            print(
                f"velocity={latest('velocity')} rpm motor_current={latest('motor_current')} A "
                f"duty={latest('duty_cycle')} v_bus={latest('bus_voltage')} V "
                f"fet_temp={latest('fet_temperature')} C"
            )

    motor.stop()
    print(f"Stopped. Final velocity={latest('velocity')} rpm")

finally:
    motor.close()
