"""Example: Output a continuous or a finite pulse train from an NI-DAQmx counter."""

import time

from instro.daq import InstroDAQ
from instro.daq.types import DAQVendor, FrequencyPulseConfig
from instro.lib.publishers import NominalCorePublisher

# Configuration: Choose your vendor. Counter output is supported on NI.
VENDOR = DAQVendor.NI

# True pulses until the train is stopped. False emits N_PULSES and stops itself.
CONTINUOUS = False
N_PULSES = 200

# Vendor-specific configuration. Each vendor driver lives in its own package and
# owns its transport at construction time.
match VENDOR:
    case DAQVendor.NI:
        from instro.daq.drivers.ni import NIDAQDriver

        TERMINAL = "/Dev1/PFI0"
        COUNTER = "Dev1/ctr0"
        driver = NIDAQDriver(device_id="Dev1")

# Nominal Core dataset to send data to as the instrument is operated.
DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.

### Main code

daq = InstroDAQ(name="myDAQ", driver=driver)
daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

with daq:
    # A 100 Hz square wave, so 200 pulses take two seconds.
    pulse_config = FrequencyPulseConfig(frequency=100.0, duty_cycle=0.5)
    if CONTINUOUS:
        daq.configure_continuous_counter_output(
            physical_channel=TERMINAL,
            counter_source=COUNTER,
            pulse_config=pulse_config,
            alias="clock",
        )
    else:
        daq.configure_finite_counter_output(
            physical_channel=TERMINAL,
            counter_source=COUNTER,
            pulse_config=pulse_config,
            n_pulses=N_PULSES,
            alias="clock",
        )

    daq.start_counter_output("clock")

    # A continuous train runs until it is stopped; a finite one reports when it is done.
    if CONTINUOUS:
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            print("Exiting main loop")
    else:
        daq.wait_for_counter_output("clock")

    daq.stop_counter_output("clock")
