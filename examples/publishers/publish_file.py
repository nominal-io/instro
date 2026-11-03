"""Example: Publishers: publish file.

Demonstrates publishing measurements to a local Avro file (FilePublisher).

"""

import io
import time

from instro.daq import InstroDAQ
from instro.daq.drivers.labjack import LabJackTSeriesDriver
from instro.lib.publishers import FilePublisher

AO_CH0 = "DAC0"
AO_CH1 = "DAC1"
AI_CH0 = "AIN0"
AI_CH1 = "AIN1"
USE_FILE_LIKE_OBJECT = False  # True: publish to an in-memory stream instead of ./captures/test_file.avro

if USE_FILE_LIKE_OBJECT:
    stream = io.BytesIO()  # Avro needs a binary stream
    file_publisher = FilePublisher(format="avro", file=stream)
else:
    file_publisher = FilePublisher(directory="./captures", format="avro", custom_file_name="test_file")

daq = InstroDAQ(
    name="myDAQ",
    driver=LabJackTSeriesDriver(device_id="440023835"),
    publishers=[file_publisher],
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

# The publisher leaves a caller's stream open, so the data is still readable after close
if USE_FILE_LIKE_OBJECT:
    print(f"Captured {len(stream.getvalue())} bytes of Avro in memory")
