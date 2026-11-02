"""Example: Run a virtual DAQ, with no hardware or vendor SDK.

VirtualDAQ simulates channels in-process, so this runs as-is. Swap in a real
driver (LabJackTSeriesDriver, NIDAQDriver, ...) and its physical channel names,
and the rest is unchanged.
"""

from instro.daq import InstroDAQ
from instro.daq.drivers import VirtualDAQ

driver = VirtualDAQ({"ai1": 3.3})  # optional: a constant reading per analog input

with InstroDAQ(name="myDAQ", driver=driver) as daq:
    daq.configure_voltage_input("ai0", alias="ch_0", range_min=0, range_max=5)
    daq.configure_voltage_input("ai1", alias="ch_1", range_min=0, range_max=5)
    daq.configure_digital_output("port0/line0", alias="do_0")
    daq.configure_digital_input("port0/line0", alias="di_0")  # same line, so it reads back do_0

    reads = daq.read_batch(["ch_0", "ch_1"])
    print(f"Channel 0: {reads['ch_0'].latest:.3f} V")
    print(f"Channel 1: {reads['ch_1'].latest:.3f} V")

    daq.write("do_0", 1)
    print(f"Digital in: {daq.read('di_0').latest:.0f}")

with InstroDAQ(name="myDAQ", driver=VirtualDAQ()) as daq:
    daq.configure_voltage_input("ai0", alias="ch_0", range_min=0, range_max=5)
    daq.configure_ai_hw_sample_rate(sample_rate=1000, samples_per_channel=100)

    # Hardware-timed: each read blocks until the next 100 samples (0.1 s at 1 kHz) are acquired.
    daq.start(background=False)
    batch = daq.read_analog()
    daq.stop()
    print(f"Hardware-timed: {len(batch.timestamps)} samples, last {batch.channel_data['myDAQ.ch_0'][-1]:.3f} V")
