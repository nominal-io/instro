"""Minimal ModbusDevice example: read and write against the sim server.

Start the sim server first:
    python -m instro.modbus.sim_server

Then in another shell:
    python -m instro.modbus.examples.minimal_example
"""

from pathlib import Path

from instro.modbus import ModbusDevice

CONFIG_PATH = Path(__file__).parent / "simulated_modbus_device.json"
CONNECTION = {"transport": "tcp", "host": "127.0.0.1", "port": 5020}


def main() -> None:
    device = ModbusDevice(config=CONFIG_PATH, connection=CONNECTION)
    with device:
        # Read a holding register (float32 temperature, seeded to 72.5 in the sim).
        m = device.read("temperature")
        print(f"temperature: {m.latest}")

        # Write a holding register. `setpoint` declares a linear scale (gain=0.1),
        # so we pass the physical value. The driver converts to raw internally.
        device.write("setpoint", 30.0)

        # Read back to confirm the write landed.
        m = device.read("setpoint")
        print(f"setpoint (after write): {m.latest}")


if __name__ == "__main__":
    main()
