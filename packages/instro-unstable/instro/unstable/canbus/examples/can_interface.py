"""Example: read and write DBC signals with CANInterface, configured from one JSON file.

bench_can.json names the adapter (a candleLight/gs_usb USB-CAN adapter on channel 0
at 500 kbit/s), the DBC sitting next to it, and the messages to receive and send.
The background daemon publishes every received message each poll_interval.

Run:
    uv run python packages/instro-unstable/instro/unstable/canbus/examples/can_interface.py
"""

import time
from pathlib import Path

from instro.unstable.canbus import CANInterface

CONFIG = Path(__file__).parent / "bench_can.json"

bus = CANInterface(CONFIG, autostart=True)
try:
    time.sleep(1.0)  # let a few BMS_Status frames arrive

    print("Pack voltage:", bus.read("BMS_Status.pack_voltage").latest, "V")

    bus.write("Motor_Command.torque_request", 5.0)
    bus.write("Motor_Command.enable", 1)  # torque_request stays at 5.0 Nm in this frame
    time.sleep(2.0)
    print("Motor speed:", bus.read("Motor_Feedback.speed").latest, "rpm")
finally:
    try:
        bus.write("Motor_Command.enable", 0)
    finally:
        bus.close()
