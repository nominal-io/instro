"""Example: Publishers: publish queued.

Demonstrates publishing measurements/commands to a dataset (Nominal Core publisher).

"""

import time

from instro.lib.publishers import NominalCorePublisher, QueuedPublisher
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.
VISA_RESOURCE = "TCPIP0::127.0.0.1::5025::SOCKET"


# Create instrument instances
psu = InstroPSU(name="myPSU", driver=SimulatedPSU(VISA_RESOURCE), num_channels=2)
core_publisher = NominalCorePublisher(dataset_rid=DATASET_RID)
queued_publisher = QueuedPublisher(core_publisher, max_queue_size=100, wait_for_queue=True)

psu.add_publisher(queued_publisher)

with psu:
    # Set up initial state of test
    psu.output_enable(False, channel=2)
    psu.set_current_limit(0.2, channel=2)
    psu.set_voltage(0, channel=2)

    psu.get_current(channel=2)
    psu.get_voltage(channel=2)

    # Start
    psu.output_enable(True, channel=2)

    for v in range(10):
        start = time.time()

        psu.set_voltage(v, channel=2)
        time.sleep(1)
        psu.get_current(channel=2)
        psu.get_voltage(channel=2)

        print(f"Time taken: {time.time() - start}")

    psu.output_enable(False, channel=2)

    print("Done, waiting for queue to be empty...")
