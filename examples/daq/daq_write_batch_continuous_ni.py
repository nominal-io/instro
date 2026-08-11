"""Example: Continuous batch write to all NI 9263 AO channels.

Change the value of CONTINUE_ON_FAILED_WRITE and unplug cDAQ to force a failed write.
Writes should continue to be attempted when CONTINUE_ON_FAILED_WRITE = True.
The whole script should error out when CONTINUE_ON_FAILED_WRITE = False.
"""

import logging
import math
import time

from instro.daq import InstroDAQ
from instro.daq.drivers.ni import NIDAQDriver
from instro.daq.types import Direction

# Show write_batch's per-channel "<alias> -> succeeded/failed" debug logs.
logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("instro").setLevel(logging.DEBUG)

DEVICE_ID = "cDAQ"  # NI device name, as defined in MAX
MODULE = f"{DEVICE_ID}Mod2"  # Slot holding the NI 9263
NUM_CHANNELS = 4  # The NI 9263 has 4 AO channels (ao0-ao3)
RANGE_MIN, RANGE_MAX = -10.0, 10.0
UPDATE_RATE_HZ = 10.0

CONTINUE_ON_FAILED_WRITE = True

### Main code

daq = InstroDAQ(name="myDAQ", driver=NIDAQDriver(device_id=DEVICE_ID))

with daq:
    aliases = [f"ao_{i}" for i in range(NUM_CHANNELS)]
    for i, alias in enumerate(aliases):
        daq.configure_analog_channel(
            direction=Direction.OUTPUT,
            physical_channel=f"{MODULE}/ao{i}",
            alias=alias,
            range_min=RANGE_MIN,
            range_max=RANGE_MAX,
        )

    start = time.time()
    while True:
        try:
            t = time.time() - start
            values = [5.0 * math.sin(2 * math.pi * 0.2 * t + i * math.pi / NUM_CHANNELS) for i in range(NUM_CHANNELS)]
            daq.write_batch(aliases, values, continue_on_failed_write=CONTINUE_ON_FAILED_WRITE)
            time.sleep(1 / UPDATE_RATE_HZ)
        except KeyboardInterrupt:
            print("Exiting main loop")
            break

    daq.write_batch(aliases, [0.0] * NUM_CHANNELS)
