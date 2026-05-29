"""Example: ModbusDevice feature showcase.

Demonstrates all config features against the sim server:
- Connection separation (connection passed to constructor, not in config)
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

import time
from pathlib import Path

from instro.lib.publishers import NominalCorePublisher
from instro.modbus import ModbusDevice

CONNECTION = {"transport": "tcp", "host": "127.0.0.1", "port": 5020, "unit_id": 1, "timeout": 2.0}
CONFIG_PATH = Path(__file__).parent / "simulated_modbus_device.json"


def main():
    device = ModbusDevice(str(CONFIG_PATH), connection=CONNECTION, autostart=True)
    device.add_publisher(
        NominalCorePublisher("ri.catalog.cerulean-staging.dataset.056356ba-5c64-479c-9fc8-da0eba27ae0b")
    )

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

        # --- Background daemon: data is polled automatically at poll_interval ---
        print("\n=== Background Daemon (polled via get_channel) ===")
        print("  Waiting for daemon to collect samples...")
        time.sleep(3)

        measurement = device.get_channel("sim.sensor_1", length=2)
        print(f"  sensor_1 (2 samples): {measurement.channel_data['sim.sensor_1']}")

        measurement = device.get_channel("sim.temperature", length=2)
        print(f"  temperature (2 samples): {measurement.channel_data['sim.temperature']}")

        # --- Continuous loop (daemon continues polling in background) ---
        print("\n=== Continuous Updates (Ctrl+C to stop) ===")
        print("  (daemon continues polling in background)\n")

        i = 0
        while True:
            temp = 70.0 + (i % 20)
            device.write("temperature", temp)
            device.write("setpoint", (i % 50))
            device.write("mode", "heat" if i % 2 == 0 else "cool")
            device.write("enable", i % 3 != 0)
            device.write("status_register", i % 32)

            # Read back from daemon buffer instead of manual read
            buffered = device.get_channel("sim.temperature", length=1)
            print(f"  tick {i}: temp={temp}, buffered={buffered.channel_data['sim.temperature'][0]}")
            i += 1
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        device.close()


if __name__ == "__main__":
    main()
