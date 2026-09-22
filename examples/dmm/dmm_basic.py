"""Example: A basic DMM reading.

Demonstrates a single-call measurement published to a Nominal dataset. Swapping
in another vendor's driver leaves the rest of the script unchanged.

"""

from instro.dmm import InstroDMM
from instro.dmm.drivers import Agilent34401A  # or Keysight34461A, Keithley2400, SimulatedDMM
from instro.lib.publishers import NominalCorePublisher
from instro.lib.transports import SerialConfig, VisaConfig

VISA_RESOURCE = "ASRL3::INSTR"
DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.

dmm = InstroDMM(
    name="myDMM",
    # Drivers also accept a bare resource string: Agilent34401A(VISA_RESOURCE).
    driver=Agilent34401A(
        VisaConfig(
            visa_resource=VISA_RESOURCE,
            serial_config=SerialConfig(baud_rate=9600),
        )
    ),
    publishers=[NominalCorePublisher(dataset_rid=DATASET_RID)],
)
with dmm:
    print(dmm.read_dc_voltage().latest)
