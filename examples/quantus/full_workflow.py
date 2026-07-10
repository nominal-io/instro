"""Example: every QuantusDevice capability against a full simulated rack.

The rack is described in rack_full.json (a `connection=` argument can override
its `connection` section for bench-specific hosts). Covers autostart,
name-from-config, Nominal Core + CSV file publishers, reconcile report (rate
snapping), streamed analog / tacho-RPM / DBC-decoded CAN channels (the `dbc`
entry on the vehicle_bus channel; decoding happens natively), runtime writes
published as Commands (auto-zero, bridge balance, settings-plane write with
epoch restart, CAN transmit), and teardown.

Start the simulator first:

    cargo run -p quantus-sim -- examples/quantus/sim_full.toml
"""

import time
from pathlib import Path

from instro.lib.publishers import NominalCorePublisher
from instro.quantus import QuantusDevice

HERE = Path(__file__).parent
DATASET_RID = "dataset_rid"  # Replace with your dataset RID.

# autostart=True: open + reconcile (one declarative pass, applied atomically)
# + start streaming, all in the constructor.
with QuantusDevice(
    config=HERE / "rack_full.json",
    publishers=[NominalCorePublisher(dataset_rid=DATASET_RID)],
    autostart=True,
) as daq:
    time.sleep(2)

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
