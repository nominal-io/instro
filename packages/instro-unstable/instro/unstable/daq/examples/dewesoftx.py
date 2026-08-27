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
CHANNELS = ["EGT1", "EGT2", "EGT3", "EGT4", "Engine RPM"]

DATASET_RID = "ri.catalog.gov-staging.dataset.fe8c2982-e631-4727-b1c3-0a5402e4858d"

daq = InstroDAQ(name="dewesoft", driver=DewesoftX())
daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID, profile="staging"))

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
