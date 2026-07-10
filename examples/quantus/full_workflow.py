"""Example: every QuantusDevice capability against a full simulated rack.

Covers: config file + connection override + name-from-config, default tags,
CSV + custom publishers, reconcile report (rate snapping), streamed analog /
tacho-RPM / DBC-decoded CAN channels, runtime writes (auto-zero, bridge
balance, settings-plane write with epoch restart, CAN transmit), and teardown.

Start the simulator first (from the quantus repo):

    cargo run -p quantus-sim -- examples/quantus/sim_full.toml

Requires the `quantus` wheel and the `can` extra (cantools).
"""

import json
import tempfile
import time
from collections import Counter
from pathlib import Path

from instro.quantus import QuantusDevice

from instro.lib.publishers import FilePublisher

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


# The rack config would normally live in its own rack.json; built inline here
# so the example is self-contained. Note "device.name" — it becomes the
# channel-name prefix unless overridden by the name= argument.
rack = {
    "device": {"name": "demo_rig"},
    "connection": {"host": "127.0.0.1", "rest_port": 9999},  # wrong on purpose; overridden below
    "system": {"master_sampling_rate": 131072, "streaming_format": "Processed"},
    "modules": [
        {
            "name": "MIC42X7",
            "sample_rate_hz": 65536.0,
            "channels": [
                {
                    "index": 1,
                    "alias": "mic_inlet",
                    "mode": "Microphone Input",
                    "streaming": True,
                    "settings": {"Voltage Range": "1.2 V"},
                },
            ],
        },
        {
            "name": "THM427",
            "sample_rate_hz": 100.0,  # not achievable: snaps to 512 (MSR/256)
            "channels": [
                {"index": 1, "alias": "tc_exhaust", "mode": "Thermocouple Type K Input", "streaming": True},
                {"index": 2, "alias": "tc_ambient", "mode": "Thermocouple Type K Input", "streaming": True},
            ],
        },
        {
            "name": "WSB42X2",
            "sample_rate_hz": 512.0,
            "channels": [
                {
                    "index": 1,
                    "alias": "strain_1",
                    "mode": "WSB Voltage Excitation",
                    "streaming": True,
                    "settings": {"Bridge Mode": "Full Bridge", "Excitation Amplitude": 5.0},
                },
            ],
        },
        {
            "name": "ICT42S6",
            "channels": [
                {"index": 1, "alias": "shaft", "mode": "Enabled", "streaming": True},
            ],
        },
        {
            "name": "CAN42S2",
            "channels": [
                {"index": 1, "alias": "vehicle_bus", "mode": "Listen Only", "streaming": True},
                {"index": 2, "alias": "vehicle_tx", "mode": "Participate"},
            ],
        },
        {
            "name": "ALO42S4",
            "channels": [
                {
                    "index": 1,
                    "alias": "shaker_drive",
                    "mode": "Sine",
                    "settings": {"Signal Amplitude": 2.0, "Signal Frequency": 100.0, "Signal Connection": "Connected"},
                },
            ],
        },
    ],
}

csv_dir = Path(tempfile.mkdtemp(prefix="quantus_demo_"))
stats = StatsPublisher()

daq = QuantusDevice(
    config=rack,
    connection={"rest_port": 8082},  # overrides the config's (wrong) port
    dbc={"vehicle_bus": str(HERE / "vehicle.dbc")},  # decode this bus's frames
    publishers=[stats, FilePublisher(csv_dir, format="csv")],
    test_stand="sim-bench",  # default tag on every Measurement
)
assert daq.name == "demo_rig"  # from config device.name

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
# epoch — expect a short data gap, which the device logs and rides through.
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
print(json.dumps({"demo": "ok"}))
