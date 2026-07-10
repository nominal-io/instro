"""Example: stream one analog channel from a (simulated) Quantus mainframe.

Start the simulator first (from the quantus repo):

    cargo run -p quantus-sim -- examples/quantus/sim_simple.toml
"""

import time

from instro.quantus import QuantusDevice


class PrintPublisher:
    """Smallest possible publisher: print what the device emits."""

    def publish(self, data, **kwargs):
        for channel, values in data.channel_data.items():
            print(f"{channel}: {len(values)} samples, first={values[0]:+.3f}")

    def close(self):
        pass


config = {
    "connection": {"host": "127.0.0.1", "rest_port": 8081},
    "system": {"master_sampling_rate": 131072},
    "modules": [
        {
            "name": "ICS425",
            "sample_rate_hz": 512.0,
            "channels": [
                {"index": 1, "alias": "accel_z", "mode": "Voltage Input", "streaming": True},
            ],
        }
    ],
}

daq = QuantusDevice(config, name="quantus", publishers=[PrintPublisher()])
daq.open()

report = daq.reconcile()
for module in report["modules"]:
    print(f"{module['name']}: requested {module['requested_hz']} Hz -> achieved {module['achieved_hz']} Hz")

daq.start()
time.sleep(3)
daq.close()
