"""Minimal ModbusDevice example: read and write against the sim server.

Start the sim server first:
    python -m instro.modbus.sim_server

Then in another shell:
    uv run python examples/modbus/minimal_example.py
"""

import json
from pathlib import Path

from instro.register import InstroRegisterInstrument
from instro.register.drivers.modbus import ModbusConfig, ModbusRegisterDriver

CONFIG_PATH = Path(__file__).parent / "simulated_modbus_device.json"
CONNECTION = {"transport": "tcp", "host": "127.0.0.1", "port": 5020}


def main() -> None:
    raw = json.loads(CONFIG_PATH.read_text())
    config = ModbusConfig.model_validate({**raw, "connection": CONNECTION})
    device = InstroRegisterInstrument(driver=ModbusRegisterDriver(config))
    device.open()
    try:
        # Read a holding register (float32 temperature, seeded to 72.5 in the sim).
        m = device.read("temperature")
        print(f"temperature: {m.latest}")

        # Write a holding register. `setpoint` declares a linear scale (gain=0.1),
        # so we pass the physical value — the driver converts to raw internally.
        device.write("setpoint", 30.0)

        # Read back to confirm the write landed.
        m = device.read("setpoint")
        print(f"setpoint (after write): {m.latest}")
    finally:
        device.close()


if __name__ == "__main__":
    main()
