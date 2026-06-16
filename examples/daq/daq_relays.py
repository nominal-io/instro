"""Example: Relay Control for DAQs with relays native to them."""

import time

from instro.daq import InstroDAQ
from instro.daq.types import DAQVendor, Direction, Logic
from instro.lib.publishers import NominalCorePublisher

# Configuration: Choose your vendor.
VENDOR = DAQVendor.KEYSIGHT_34980

# Vendor-specific configuration. Each vendor driver lives in its own package and
# owns its transport at construction time.
match VENDOR:
    case DAQVendor.KEYSIGHT_34980:
        from instro.daq.drivers import Keysight34980A

        CHANNEL_0 = "8001"
        CHANNEL_1 = "8002"
        driver = Keysight34980A("USB0::0x0957::0x0507::MY44001757::INSTR")

# Nominal Core dataset to send data to as the instrument is operated.
DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.

### Main code

daq = InstroDAQ(name="myDAQ", driver=driver)
daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

with daq:
    daq.configure_relay_channel(physical_channel=CHANNEL_0, alias="relay0")
    daq.configure_relay_channel(physical_channel=CHANNEL_1, alias="relay1")
    daq.close_relay("relay0")
    time.sleep(1)
    daq.close_relay("relay1")
    time.sleep(1)
    daq.open_relay("relay0")
    time.sleep(1)
    daq.open_relay("relay1")
