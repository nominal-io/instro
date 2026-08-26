"""Closing Bell - Step 2: MVP smoke test."""

import time

from instro.lib.publishers import NominalCorePublisher
from instro.unstable.awg import InstroAWG
from instro.unstable.awg.drivers import Keysight33521B
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    BurstTriggerSource,
    BurstType,
    GatePolarity,
    ModulationType,
    Pulse,
    Sine,
    Square,
    SweepTriggerSource,
    SweepType,
    Triangle,
)

VISA_RESOURCE = "USB0::0x0957::0x2B07::MY52702203::INSTR"
DATASET_RID = None
NUM_CHANNELS = 1
WAIT_TIME = 0.5

awg = InstroAWG(name="KeysightAWG", driver=Keysight33521B(visa_resource=VISA_RESOURCE), num_channels=NUM_CHANNELS)
if DATASET_RID:
    awg.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

awg.open()
try:
    awg.set_waveform(1, Sine(frequency_hz=1_000.0, phase_deg=0.0))
    print("waveform:", awg.get_waveform(1))

    awg.set_amplitude(1, 2.0, AmplitudeMeasurementUnit.VPP)
    print("amplitude:", awg.get_amplitude(1))

    print("2 VPP in VRMS:", awg.convert_amplitude(
        1, 2.0, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS))

    awg.set_offset(1, 0.5)
    print("offset:", awg.get_offset(1).latest)

    awg.set_output_load(1, 50.0)
    print("load (50 ohm):", awg.get_output_load(1).latest)
    awg.set_output_load(1, None)
    print("load (high-Z):", awg.get_output_load(1).latest)
    awg.set_output_load(1, 50.0)

    awg.output_enable(1, True)
    print("output enabled:", awg.get_output_state(1).latest)

    awg.start()
    time.sleep(WAIT_TIME)
    awg.stop()
finally:
    for ch in range(1, NUM_CHANNELS + 1):
        awg.output_enable(ch, False)
    awg.close()
