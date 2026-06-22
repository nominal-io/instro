"""Example: ELoad: eload.

Demonstrates publishing measurements/commands to a dataset (Nominal Core publisher).

"""

import time

from instro.eload import InstroELoad
from instro.eload.drivers.bk_85xxb import BK85XXB
from instro.eload.types import LoadMode
from instro.lib.publishers import NominalCorePublisher
from instro.lib.transports import SerialConfig, VisaConfig

VISA_RESOURCE = "ASRL7::INSTR"
DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.

eload = InstroELoad(
    name="myELoad",
    driver=BK85XXB(
        VisaConfig(
            visa_resource=VISA_RESOURCE,
            serial_config=SerialConfig(baud_rate=9600),
        )
    ),
)

eload.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

eload.background_interval = 0.5  # query eload for new values every half second.

with eload:
    # This launches a background daemon that queries the measured voltage, current, and output state.
    eload.start()

    # Allow the daemon to publish some current-state measurements before reconfiguring the outputs.
    time.sleep(1)

    eload.set_mode(mode=LoadMode.CC)
    eload.set_range(value=1.5)
    eload.set_level(value=0.4)
    eload.output_enable(enable=True)

    time.sleep(0.5)

    eload.set_level(value=0.2)

    time.sleep(0.5)

    eload.set_level(value=1.1)

    time.sleep(0.5)

    eload.output_enable(enable=False)

    time.sleep(0.5)

    eload.output_enable(enable=False)
