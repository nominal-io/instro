"""Example: stream one analog channel from a (simulated) Quantus mainframe.

The rack is described in rack_simple.json. Start the simulator first:

    cargo run -p quantus-sim -- examples/quantus/sim_simple.toml
"""

import time
from pathlib import Path

from instro.quantus import QuantusDevice

HERE = Path(__file__).parent


class PrintPublisher:
    """Smallest possible publisher: print what the device emits."""

    def publish(self, data, **kwargs):
        for channel, values in data.channel_data.items():
            print(f"{channel}: {len(values)} samples, first={values[0]:+.3f}")

    def close(self):
        pass


daq = QuantusDevice(HERE / "rack_simple.json", publishers=[PrintPublisher()])
daq.open()

report = daq.reconcile()
for module in report["modules"]:
    print(f"{module['name']}: requested {module['requested_hz']} Hz -> achieved {module['achieved_hz']} Hz")

daq.start()
time.sleep(3)
daq.close()
