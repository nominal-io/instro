"""Example: stream live DewesoftX channels to a Nominal Core dataset.

Requires a running DewesoftX instance on this machine that is acquiring and storing.
"""

import logging
import time

from instro.daq import InstroDAQ
from instro.daq.types import Direction
from instro.lib.publishers import NominalCorePublisher
from instro.unstable.daq.drivers import DewesoftX

# Show instro logs, including the driver's store-session re-anchor notices.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# DewesoftX channel names as shown in the channel setup, set to "Used".
CHANNELS = ["AI 1", "AI 2"]

# Nominal Core dataset to send data to as the instrument is operated.
DATASET_RID = (
    "ri.catalog.cerulean-staging.dataset.cc35d3b9-9862-46c5-9bef-7ac76455c97d"  # Replace with your dataset RID.
)

daq = InstroDAQ(name="dewesoft", driver=DewesoftX())
daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

with daq:
    for channel in CHANNELS:
        daq.configure_analog_channel(direction=Direction.INPUT, physical_channel=channel)

    # DewesoftX owns the hardware sample clock; this rate only paces the polling daemon,
    # and every poll drains all new samples with their absolute timestamps.
    daq.configure_ai_sw_sample_rate(sample_rate=5)

    # Software-timed acquisition: start() spins the background daemon, which drains the
    # DewesoftX buffers every period and publishes each batch to the dataset.
    daq.start()

    print("Streaming to Nominal Core; press Ctrl+C to stop.")
    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            print("Exiting")
            break

    daq.stop()
