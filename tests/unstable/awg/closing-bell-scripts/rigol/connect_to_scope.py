"""Closing Bell - drive a waveform on the Rigol DG1022Z while watching it on a scope."""

import time

from instro.lib.publishers import NominalCorePublisher
from instro.lib.transports.visa import VisaDriver
from instro.scope import InstroScope
from instro.scope.drivers import Tektronix2SeriesMSO
from instro.unstable.awg import InstroAWG
from instro.unstable.awg.drivers import RigolDG1022Z
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

AWG_VISA_RESOURCE = "USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR"
SCOPE_VISA_RESOURCE = "USB0::0x0699::0x0105::SGVJ016092::INSTR"
DATASET_RID = None
AWG_NUM_CHANNELS = 2
SCOPE_NUM_CHANNELS = 4
CHANNEL = 1
RUN_TIME = 30
RESET_ON_START = True

if RESET_ON_START:
    reset_awg = VisaDriver(AWG_VISA_RESOURCE)
    reset_awg.open()
    reset_awg.write("*RST")
    reset_awg.query("*OPC?")
    reset_awg.write("*CLS")
    reset_awg.close()

    reset_scope = VisaDriver(SCOPE_VISA_RESOURCE)
    reset_scope.open()
    reset_scope.write("*RST")
    reset_scope.query("*OPC?")
    reset_scope.write("*CLS")
    reset_scope.close()

awg = InstroAWG(name="RigolAWG", driver=RigolDG1022Z(visa_resource=AWG_VISA_RESOURCE), num_channels=AWG_NUM_CHANNELS)
if DATASET_RID:
    awg.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

scope = InstroScope(
    name="Scope", driver=Tektronix2SeriesMSO(visa_resource=SCOPE_VISA_RESOURCE), num_channels=SCOPE_NUM_CHANNELS
)

awg.open()
scope.open()
try:
    awg.set_waveform(CHANNEL, Square(frequency_hz=1_000.0))
    awg.set_amplitude(CHANNEL, 2.0, AmplitudeMeasurementUnit.VPP)
    awg.output_enable(CHANNEL, True)

    scope.run()
    print(f"watch channel {CHANNEL} on the scope for {RUN_TIME}s")
    time.sleep(RUN_TIME)
finally:
    awg.output_enable(CHANNEL, False)
    scope.stop_acquisition()
    awg.close()
    scope.close()
