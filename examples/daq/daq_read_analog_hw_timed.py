"""Example: DAQ read analog HW timed.

Demonstrates publishing measurements/commands to a dataset (Nominal Core publisher).

"""

import time

from instro.daq import InstroDAQ
from instro.daq.types import DAQVendor, Direction
from instro.lib.publishers import NominalCorePublisher

# Configuration: Choose your vendor.
VENDOR = DAQVendor.LABJACK_T_SERIES

# Vendor-specific configuration. Each vendor driver lives in its own package and
# owns its transport at construction time.
match VENDOR:
    case DAQVendor.LABJACK_T_SERIES:
        from instro.daq.drivers.labjack import LabJackTSeriesDriver

        CHANNEL_0 = "AIN0"
        CHANNEL_1 = "AIN1"
        driver = LabJackTSeriesDriver(device_id="440020473")  # LabJack serial number
    case DAQVendor.NI:
        from instro.daq.drivers.ni import NIDAQDriver

        CHANNEL_0 = "Dev1/ai0"
        CHANNEL_1 = "Dev1/ai1"
        driver = NIDAQDriver(device_id="Dev1")  # NI device name, as defined in MAX
    case DAQVendor.KEYSIGHT_34980:
        from instro.daq.drivers import Keysight34980A

        CHANNEL_0 = "1009"
        CHANNEL_1 = "1010"
        driver = Keysight34980A("USB0::0x0957::0x0507::MY44001757::INSTR")  # VISA resource
    case DAQVendor.MCC:
        from instro.daq.drivers.mcc import MCCDriver

        CHANNEL_0 = "0"
        CHANNEL_1 = "1"
        driver = MCCDriver(
            device_id="344371:0"
        )  # MCC DAQ device ID, optionally suffixed with ":<board_number>" (default 0)

# Nominal Core dataset to send data to as the instrument is operated.
DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.

### Main code

daq = InstroDAQ(name="myDAQ", driver=driver)
daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

with daq:
    daq.configure_analog_channel(
        direction=Direction.INPUT, physical_channel=CHANNEL_0, alias=f"ch_0", range_min=0, range_max=5
    )
    daq.configure_analog_channel(
        direction=Direction.INPUT, physical_channel=CHANNEL_1, alias=f"ch_1", range_min=0, range_max=5
    )

    daq.configure_ai_sample_rate(sample_rate=100)

    # Start the acquisition.
    # This launches a background daemon that fetches samples from the DAQ device buffer.
    daq.start()

    while True:
        try:
            ch_1 = daq.get_channel("myDAQ.ch_0", 1, True)  # This will block for the latest sample
            ch_2 = daq.get_channel("myDAQ.ch_1", 10, False)  # This will immediately return with 10 samples.
            print(f"Channel 1 latest: {ch_1.latest}")
            print(f"Channel 1 samples: {ch_1.values}")
            print(f"Channel 2 latest: {ch_2.latest}")
            print(f"Channel 2 samples: {ch_2.values}")
        except KeyboardInterrupt:
            print("Exiting main loop")
            break

    daq.stop()
