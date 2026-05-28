"""Example: ModbusDevice feature showcase.

Demonstrates all config features against the sim server:
- Connection separation (connection merged from memory at runtime, not in config)
- Timing config (poll_interval + write_delay_ms)
- Read groups (batched register reads)
- Write limits (write_min / write_max)
- Write value maps (string-to-number mapping)
- Scaled registers (linear gain + offset)
- Bitmap extraction (individual bit channels)
- All register types (holding, input, coil, discrete)

Start the sim server first:
    python -m instro.modbus.sim_server

Then run this script.
"""

import json
from pathlib import Path

from instro.register import InstroRegisterInstrument
from instro.register.drivers.modbus import ModbusConfig, ModbusRegisterDriver
from instro.utils.publishers import NominalCorePublisher

CONNECTION = {"transport": "tcp", "host": "127.0.0.1", "port": 5020, "unit_id": 1, "timeout": 2.0}
CONFIG_PATH = Path(__file__).parent / "simulated_modbus_device.json"


def main():
    raw = json.loads(CONFIG_PATH.read_text())
    config = ModbusConfig.model_validate({**raw, "connection": CONNECTION})
    device = InstroRegisterInstrument(driver=ModbusRegisterDriver(config))
    try:
        device.add_publisher(
            NominalCorePublisher("ri.catalog.cerulean-staging.dataset.056356ba-5c64-479c-9fc8-da0eba27ae0b")
        )
    except (RuntimeError, ConnectionError) as e:
        print(f"Warning: Nominal publisher unavailable, continuing without it: {e}")
    device.open()

    try:
        # --- Read groups: sensor_1 and sensor_2 are read in a single Modbus transaction ---
        print("=== Read Groups (input_sensors) ===")
        print(f"  sensor_1: {device.read('sensor_1')}")
        print(f"  sensor_2: {device.read('sensor_2')}")

        # --- Scaled registers ---
        print("\n=== Scaled Registers ===")
        print(f"  raw_count (gain=0.001): {device.read('raw_count')}")
        print(f"  setpoint  (gain=0.1):   {device.read('setpoint')}")

        # --- Bitmap extraction ---
        print("\n=== Bitmap Register ===")
        status = device.read("status_register")
        for name, value in status.channel_data.items():
            print(f"  {name}: {value}")

        # --- Write value map: write strings instead of magic numbers ---
        print("\n=== Write Value Map ===")
        for mode_name in ("off", "standby", "heat", "cool", "auto"):
            device.write("mode", mode_name)
            readback = device.read("mode").channel_data["sim.mode"][0]
            print(f"  Wrote mode='{mode_name}' -> read back: {readback}")

        # --- Write limits: fat-finger protection ---
        print("\n=== Write Limits ===")
        device.write("temperature", 150.0)
        print(f"  Wrote temperature=150.0 -> read back: {device.read('temperature')}")
        try:
            device.write("temperature", 999.0)
        except ValueError as e:
            print(f"  Rejected temperature=999.0: {e}")

        # --- Coils (read/write boolean) ---
        print("\n=== Coils ===")
        device.write("enable", True)
        print(f"  enable: {device.read('enable')}")
        device.write("reset", True)
        print(f"  reset:  {device.read('reset')}")
        device.write("reset", False)

        # --- Discrete inputs (read-only boolean, grouped) ---
        print("\n=== Discrete Inputs (grouped read) ===")
        print(f"  power_good:  {device.read('power_good')}")
        print(f"  overtemp:    {device.read('overtemp')}")
        print(f"  door_closed: {device.read('door_closed')}")

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        device.close()


if __name__ == "__main__":
    main()
