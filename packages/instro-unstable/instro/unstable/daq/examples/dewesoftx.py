"""Example: stream live DewesoftX channels to a Nominal Core dataset.

Requires a running DewesoftX instance on this machine that is acquiring.
"""

import logging
import time

from instro.daq import InstroDAQ
from instro.lib.publishers import NominalCorePublisher
from instro.unstable.daq.drivers import DewesoftX

# Show instro logs, including the driver's store-session re-anchor notices.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# DewesoftX channel names as shown in the channel setup, set to "Used".
CHANNELS = ["AI 1", "AI 2"]

DATASET_RID = "ri.catalog.cerulean-staging.dataset.cc35d3b9-9862-46c5-9bef-7ac76455c97d"

daq = InstroDAQ(name="dewesoft", driver=DewesoftX())
daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

with daq:
    for channel in CHANNELS:
        daq.configure_voltage_input(channel)

    # Input sample rate is discarded and the sample rate from DewesoftX is used
    daq.configure_ai_sample_rate(sample_rate=5000)

    # Start "hardware timed" acquisition of data from DewesoftX
    # We treat DewesoftX as the hardware buffer here
    daq.start()

    print("Streaming to Nominal Core; press Ctrl+C to stop.")
    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            print("Exiting")
            break

    # Halts the daemon, then routes to the driver, which ends the stored session.
    daq.stop()
