"""Example: Publishers: publish file stream.

Demonstrates publishing measurements to an open file stream (FilePublisher with ``file=``).

"""

import time
from pathlib import Path

from instro import InstroDAQ
from instro.drivers import LabJackTSeriesDriver
from instro.lib import FilePublisher

AO_CH0 = "DAC0"
AO_CH1 = "DAC1"
AI_CH0 = "AIN0"
AI_CH1 = "AIN1"
OUTPUT_PATH = Path("./captures/test_file_stream.csv")

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

# The caller opens and closes the stream; CSV needs a text stream opened with newline="", or io.StringIO()
with open(OUTPUT_PATH, "w", newline="") as stream:
    daq = InstroDAQ(
        name="myDAQ",
        driver=LabJackTSeriesDriver(device_id="440023835"),
        publishers=[FilePublisher(format="csv", file=stream)],
    )

    with daq:
        daq.configure_voltage_input(physical_channel=AI_CH0, alias="ai_0", range_min=0, range_max=5)
        daq.configure_voltage_input(physical_channel=AI_CH1, alias="ai_1", range_min=0, range_max=5)
        daq.configure_voltage_output(physical_channel=AO_CH0, alias="ao_0", range_min=0, range_max=5)
        daq.configure_voltage_output(physical_channel=AO_CH1, alias="ao_1", range_min=0, range_max=5)
        daq.configure_ai_hw_sample_rate(sample_rate=100)

        daq.start()

        while True:
            try:
                ai_0 = daq.get_channel("myDAQ.ai_0", 1, True).latest
                ai_1 = daq.get_channel("myDAQ.ai_1", 1, True).latest
                print(f"ai_0: {ai_0}  ai_1: {ai_1}")
                time.sleep(1)
            except KeyboardInterrupt:
                break
        daq.stop()

    # The publisher leaves the stream open, so it is still usable after the DAQ closes
    print(f"Wrote {stream.tell()} bytes to {OUTPUT_PATH}")
