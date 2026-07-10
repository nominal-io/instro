"""Example: every QuantusDevice capability against a full simulated rack.

The rack is described in rack_full.json - deliberately without a `connection`
section, so the same file works on any bench: the connection is supplied at
runtime via the `connection=` argument. Covers name-from-config, default tags,
Nominal Core + CSV file publishers, reconcile report (rate snapping), streamed
analog / tacho-RPM / DBC-decoded CAN channels (the `dbc` entry on the
vehicle_bus channel; decoding happens natively), runtime writes published as
Commands (auto-zero, bridge balance, settings-plane write with epoch restart,
CAN transmit), and teardown.

Start the simulator first:

    cargo run -p quantus-sim -- examples/quantus/sim_full.toml
"""

import tempfile
import time
from pathlib import Path

from instro.lib.publishers import FilePublisher, NominalCorePublisher
from instro.quantus import QuantusDevice

HERE = Path(__file__).parent
DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.

csv_dir = Path(tempfile.mkdtemp(prefix="quantus_demo_"))

daq = QuantusDevice(
    config=HERE / "rack_full.json",
    connection={"host": "127.0.0.1", "port": 8082},  # bench-specific, not in the rack file
    publishers=[
        NominalCorePublisher(dataset_rid=DATASET_RID),
        FilePublisher(csv_dir, format="csv"),  # local CSV copy of the same channels
    ],
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
# Each publishes a Command on {name}.{channel}.<action>.cmd alongside the data.
daq.auto_zero()  # whole system
daq.bridge_balance("strain_1")  # one WSB channel
daq.can_transmit("vehicle_tx", [{"Id": 0x123, "Data": [1, 2, 3, 4]}])

# Settings-plane write: goes through PUT + apply, so it restarts the streaming
# epoch - expect a short data gap, which the device logs and rides through.
restarted = daq.write_settings("shaker_drive", {"Signal Amplitude": 5.0})
print(f"shaker_drive amplitude -> 5.0 V (epoch restarted: {restarted})")
time.sleep(2)

# ---- spot-check locally before teardown (full stream is in Nominal + CSV) ----
for channel in ("demo_rig.mic_inlet", "demo_rig.shaft", "demo_rig.vehicle_bus.EngineSpeed"):
    latest = daq.get_channel(channel)
    print(f"{channel}: latest = {latest.channel_data[channel][-1]:.3f}")

daq.close()
print(f"\nCSV written to {csv_dir}")
print("demo: ok")
