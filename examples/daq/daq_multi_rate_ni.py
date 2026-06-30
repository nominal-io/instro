"""Example: NI multi-rate acquisition with two InstroDAQ instances.

Runs two analog input tasks at different hardware sample rates on one NI
CompactDAQ chassis by giving each task its own InstroDAQ instance. Each
task streams five channels; both instances target the same device (cDAQ1)
with non-overlapping channels. Each instance carries its own sample rate,
hardware buffer, and background daemon, started and stopped independently.

How to partition channels across tasks, and how many tasks a device can run
concurrently, is device specific.
"""

import time

from instro.daq import InstroDAQ
from instro.daq.drivers.ni import NIDAQDriver
from instro.daq.types import Direction

# NI device name, as defined in NI MAX. Both instances share the device
# with non-overlapping channels.
DEVICE = "cDAQ"
CHANNELS_PER_TASK = 3
FAST_MODULE = f"{DEVICE}Mod1"
SLOW_MODULE = f"{DEVICE}Mod2"

FAST_SAMPLE_RATE = 50000  # Hz
SLOW_SAMPLE_RATE = 1000  # Hz

### Main code

daq_fast = InstroDAQ(name="daqFast", driver=NIDAQDriver(device_id=DEVICE))
daq_slow = InstroDAQ(name="daqSlow", driver=NIDAQDriver(device_id=DEVICE))

with daq_fast, daq_slow:
    # A physical channel belongs to exactly one instance; allocate without overlap.
    # Each instance streams CHANNELS_PER_TASK channels from its own module.
    for i in range(CHANNELS_PER_TASK):
        daq_fast.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=f"{FAST_MODULE}/ai{i}",
            alias=f"fast_channel{i}",
            range_min=-5,
            range_max=5,
        )
        daq_slow.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=f"{SLOW_MODULE}/ai{i}",
            alias=f"slow_channel{i}",
            range_min=0,
            range_max=5,
        )

    # Each instance gets its own hardware sample rate.
    daq_fast.configure_ai_sample_rate(sample_rate=FAST_SAMPLE_RATE)
    daq_slow.configure_ai_sample_rate(sample_rate=SLOW_SAMPLE_RATE)

    # Each start() launches that instance's own background daemon.
    daq_fast.start()
    daq_slow.start()

    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            print("Exiting main loop")
            break

    # The acquisitions are independent: stopping one does not affect the other.
    daq_fast.stop()
    daq_slow.stop()
