"""Example: every QuantusDevice capability against a full simulated rack.

The rack is described in rack_full.json - deliberately without a `connection`
section, so the same file works on any bench: the connection is supplied at
runtime via the `connection=` argument. Covers name-from-config, default tags,
CSV + custom publishers, reconcile report (rate snapping), streamed analog /
tacho-RPM / DBC-decoded CAN channels, runtime writes (auto-zero, bridge
balance, settings-plane write with epoch restart, CAN transmit), and teardown.

Start the simulator first:

    cargo run -p quantus-sim -- examples/quantus/sim_full.toml

Requires cantools (the `can` extra): uv run --with cantools python ...
"""

import tempfile
import time
from collections import Counter
from pathlib import Path

from instro.lib.publishers import FilePublisher
from instro.quantus import QuantusDevice

HERE = Path(__file__).parent


class StatsPublisher:
    """Count published points per channel so the demo can summarize itself."""

    def __init__(self):
        self.points = Counter()

    def publish(self, data, **kwargs):
        for channel, values in data.channel_data.items():
            self.points[channel] += len(values)

    def close(self):
        pass


csv_dir = Path(tempfile.mkdtemp(prefix="quantus_demo_"))
stats = StatsPublisher()

daq = QuantusDevice(
    config=HERE / "rack_full.json",
    connection={"host": "127.0.0.1", "rest_port": 8082},  # bench-specific, not in the rack file
    dbc={"vehicle_bus": str(HERE / "vehicle.dbc")},  # decode this bus's frames
    publishers=[stats, FilePublisher(csv_dir, format="csv")],
    test_stand="sim-bench",  # default tag on every Measurement
)
assert daq.name == "demo_rig"  # from the rack file's device.name

# ---- configure: one declarative pass, applied atomically ----
daq.open()
report = daq.reconcile()
print(f"QServer {report['version']}; epoch restart on apply: {report['restart_required']}")
for module in report["modules"]:
    if module["requested_hz"] and module["requested_hz"] != module["achieved_hz"]:
        print(
            f"  NOTE {module['name']}: {module['requested_hz']} Hz not achievable, "
            f"snapped to {module['achieved_hz']} Hz (divisor {module['divisor']})"
        )

# ---- stream ----
daq.start()
time.sleep(2)

# ---- runtime writes (safe while streaming: dedicated endpoints, no apply) ----
daq.auto_zero()  # whole system
daq.bridge_balance("strain_1")  # one WSB channel
daq.can_transmit("vehicle_tx", [{"Id": 0x123, "Data": [1, 2, 3, 4]}])

# Settings-plane write: goes through PUT + apply, so it restarts the streaming
# epoch - expect a short data gap, which the device logs and rides through.
restarted = daq.write_settings("shaker_drive", {"Signal Amplitude": 5.0})
print(f"shaker_drive amplitude -> 5.0 V (epoch restarted: {restarted})")
time.sleep(2)

daq.close()

# ---- what we captured ----
print(f"\nPublished channels ({sum(stats.points.values())} points total):")
for channel, count in sorted(stats.points.items()):
    print(f"  {channel}: {count}")
print(f"\nCSV written to {csv_dir}")

expected = [
    "demo_rig.mic_inlet",  # analog batches, 65536 Hz
    "demo_rig.tc_exhaust",  # analog, snapped to 512 Hz
    "demo_rig.strain_1",  # analog, bridge channel
    "demo_rig.shaft",  # tacho edges converted to RPM
    "demo_rig.vehicle_bus.EngineSpeed",  # DBC-decoded CAN signal
    "demo_rig.vehicle_bus.CoolantTemp",  # DBC-decoded CAN signal
]
missing = [channel for channel in expected if stats.points[channel] == 0]
assert not missing, f"expected data on {missing}"
print("demo: ok")
