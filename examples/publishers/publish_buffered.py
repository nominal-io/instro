"""Example: Publishers: publish buffered.

Demonstrates publishing measurements/commands to a dataset (Nominal Core publisher).

"""

import time

from instro.lib.publishers import BasicBufferedPublisher, NominalCorePublisher
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.
VISA_RESOURCE = "TCPIP0::127.0.0.1::5025::SOCKET"


# Create instrument instances
psu = InstroPSU(name="myPSU", driver=SimulatedPSU(VISA_RESOURCE), num_channels=2)
core_publisher = NominalCorePublisher(dataset_rid=DATASET_RID)
buffered_publisher = BasicBufferedPublisher(core_publisher, buffer_size=20)  # example only
# NominalCorePublish has a batch_size parameter that does buffered sending to Nominal Core through nominal library.
# Therefore, this is an example of the composition use of BufferedPulisher to add buffered capability.

psu.add_publisher(buffered_publisher)

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
        psu.set_voltage(v, channel=2)
        time.sleep(1)  # Simulate some delay
        psu.get_current(channel=2)  # This value will be printed
        psu.get_voltage(channel=2)  # This value will not be printed

    psu.output_enable(False, channel=2)
