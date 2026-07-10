"""Example: stream one analog channel from a (simulated) Quantus mainframe.

The rack is described in rack_simple.json. Start the simulator first:

    cargo run -p quantus-sim -- examples/quantus/sim_simple.toml
"""

import time
from pathlib import Path

from instro.quantus import QuantusDevice

CONFIG_PATH = Path(__file__).parent / "rack_simple.json"

daq = QuantusDevice(config=CONFIG_PATH, autostart=True)

time.sleep(10)

print(daq.get_channel("quantus_demo.accel_z", length=10))

daq.close()
