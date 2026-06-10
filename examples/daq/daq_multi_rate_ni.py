"""Example: NI multi-rate acquisition with two InstroDAQ instances.

Runs two analog input tasks at different hardware sample rates on one NI
CompactDAQ chassis by giving each task its own InstroDAQ instance. Both
instances target the same device (cDAQ1); the channels are allocated per
module so the two tasks never share a physical channel. Each instance
carries its own sample rate, hardware buffer, and background daemon,
started and stopped independently.

How many tasks a device can run concurrently is device specific; see the
"Multiple InstroDAQ instances on NI" guide.
"""

import time

from instro.daq import InstroDAQ
from instro.daq.drivers.ni import NIDAQDriver
from instro.daq.types import Direction

# NI device name, as defined in NI MAX. Both instances share the device;
# each instance owns the channels of one module.
DEVICE = "cDAQ1"
FAST_CHANNEL = f"{DEVICE}Mod1/ai0"
SLOW_CHANNEL = f"{DEVICE}Mod2/ai0"

FAST_SAMPLE_RATE = 1000  # Hz
SLOW_SAMPLE_RATE = 10  # Hz

### Main code

daq_fast = InstroDAQ(name="daqFast", driver=NIDAQDriver(device_id=DEVICE))
daq_slow = InstroDAQ(name="daqSlow", driver=NIDAQDriver(device_id=DEVICE))

daq_fast.open()
daq_slow.open()

try:
    # A physical channel belongs to exactly one instance; allocate without overlap.
    daq_fast.configure_analog_channel(
        direction=Direction.INPUT, physical_channel=FAST_CHANNEL, alias="vibration", range_min=-5, range_max=5
    )
    daq_slow.configure_analog_channel(
        direction=Direction.INPUT, physical_channel=SLOW_CHANNEL, alias="temperature", range_min=0, range_max=5
    )

    # Each instance gets its own hardware sample rate.
    daq_fast.configure_ai_sample_rate(sample_rate=FAST_SAMPLE_RATE)
    daq_slow.configure_ai_sample_rate(sample_rate=SLOW_SAMPLE_RATE)

    # Each start() launches that instance's own background daemon.
    daq_fast.start()
    daq_slow.start()

    while True:
        try:
            vibration = daq_fast.get_channel("daqFast.vibration", 100, True)  # Block for the latest sample
            temperature = daq_slow.get_channel("daqSlow.temperature", 1, False)  # Return immediately
            print(f"vibration latest: {vibration.latest} ({len(vibration.values)} samples)")
            print(f"temperature latest: {temperature.latest}")
            time.sleep(0.5)
        except KeyboardInterrupt:
            print("Exiting main loop")
            break

    # The acquisitions are independent: stopping one does not affect the other.
    daq_fast.stop()
    daq_slow.stop()
finally:
    print("Closing DAQs")
    daq_fast.close()
    daq_slow.close()
