"""Example: two ModbusDevices sharing one Modbus TCP connection.

Demonstrates the shared-bus pattern: one ModbusTCPTransport passed to each
device, with each device's own `unit_id`. The transport opens once, for
whichever device opens first, and stays open until the last device closes it --
the way one RS-485 multi-drop line (or a TCP-to-serial gateway fronting one)
serves several unit addresses over a single connection.

Start the sim server first:
    python -m instro.modbus.sim_server

Then run this script.
"""

from pathlib import Path

from instro.lib.transports import ModbusTCPTransport
from instro.modbus import ModbusDevice

CONFIG_PATH = Path(__file__).parent / "simulated_modbus_device.json"


def main() -> None:
    transport = ModbusTCPTransport(host="127.0.0.1", port=5020)

    unit_1 = ModbusDevice(config=CONFIG_PATH, connection=transport, unit_id=1, name="unit_1")
    unit_2 = ModbusDevice(config=CONFIG_PATH, connection=transport, unit_id=2, name="unit_2")

    unit_1.open()
    unit_2.open()  # shares the connection unit_1 already opened; no second socket

    try:
        print(f"unit_1 temperature: {unit_1.read('temperature')}")
        print(f"unit_2 temperature: {unit_2.read('temperature')}")

        unit_1.write("setpoint", 42.0)
        print(f"unit_1 setpoint (after write): {unit_1.read('setpoint')}")
        print(f"unit_2 setpoint (independent unit, same wire): {unit_2.read('setpoint')}")
    finally:
        unit_1.close()  # connection stays open: unit_2 still holds it
        unit_2.close()  # last owner leaves: connection tears down


if __name__ == "__main__":
    main()
