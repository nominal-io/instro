"""Example: Read a counter input on an NI cDAQ, polled by the background daemon."""

from instro.daq import InstroDAQ
from instro.daq.types import CounterMeasurement, DAQVendor
from instro.lib.publishers import NominalCorePublisher

# Configuration: Choose your vendor. Counter input is supported on NI.
VENDOR = DAQVendor.NI

# What the counter measures: PULSE_COUNT (counts), FREQUENCY (Hz), PERIOD or PULSE_WIDTH (seconds).
MEASUREMENT = CounterMeasurement.FREQUENCY

# Rate the background daemon polls the counter at.
SAMPLE_RATE = 10

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
    # Edge counting has no range. The timed measurements take one in their own unit.
    match MEASUREMENT:
        case CounterMeasurement.PULSE_COUNT:
            daq.configure_pulse_count_counter_input(
                physical_channel=TERMINAL,
                counter_source=COUNTER,
                alias="counter",
            )
        case CounterMeasurement.FREQUENCY:
            daq.configure_frequency_counter_input(
                physical_channel=TERMINAL,
                counter_source=COUNTER,
                range_min=1.0,
                range_max=10_000.0,
                alias="counter",
            )
        case CounterMeasurement.PERIOD:
            daq.configure_period_counter_input(
                physical_channel=TERMINAL,
                counter_source=COUNTER,
                range_min=1e-4,
                range_max=1.0,
                alias="counter",
            )
        case CounterMeasurement.PULSE_WIDTH:
            daq.configure_pulse_width_counter_input(
                physical_channel=TERMINAL,
                counter_source=COUNTER,
                range_min=1e-4,
                range_max=1.0,
                alias="counter",
            )

    # There is no device sample clock here: this rate paces the background daemon.
    daq.configure_ai_sw_sample_rate(sample_rate=SAMPLE_RATE)

    # Start the acquisition. This launches the daemon that polls the counter every period.
    daq.start()

    while True:
        try:
            counter = daq.read("counter")  # Polls the counter and publishes the sample
            print(f"{MEASUREMENT.value}: {counter.latest}")
        except KeyboardInterrupt:
            print("Exiting main loop")
            break

    daq.stop()
