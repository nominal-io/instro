"""Example: Output a finite pulse train from an NI-DAQmx counter."""

from instro.daq import InstroDAQ
from instro.daq.types import DAQVendor, FrequencyPulseConfig
from instro.lib.publishers import NominalCorePublisher

# Configuration: Choose your vendor. Counter output is supported on NI.
VENDOR = DAQVendor.NI

# Vendor-specific configuration. Each vendor driver lives in its own package and
# owns its transport at construction time.
match VENDOR:
    case DAQVendor.NI:
        from instro.daq.drivers.ni import NIDAQDriver

        TERMINAL = "/Dev1/PFI2"
        COUNTER = "Dev1/ctr0"
        driver = NIDAQDriver(device_id="Dev1")

# Nominal Core dataset to send data to as the instrument is operated.
DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.

### Main code

daq = InstroDAQ(name="myDAQ", driver=driver)
# daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

with daq:
    # The train lasts two seconds: 200 pulses at 100 Hz.
    daq.configure_finite_counter_output(
        physical_channel=TERMINAL,
        counter_source=COUNTER,
        pulse_config=FrequencyPulseConfig(frequency=100.0, duty_cycle=0.5),
        n_pulses=200,
        alias="clock",
    )

    daq.start_counter_output("clock")

    # The wait blocks for the train's own duration, plus a second of slack.
    daq.wait_for_counter_output("clock")

    daq.stop_counter_output("clock")
