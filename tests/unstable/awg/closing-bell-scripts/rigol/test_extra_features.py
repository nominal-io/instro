"""Closing Bell - Step 3: modulation, burst, and sweep on the Rigol DG1022Z."""

import time

from instro.lib.publishers import NominalCorePublisher
from instro.lib.transports.visa import VisaDriver
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

VISA_RESOURCE = "USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR"
DATASET_RID = None
NUM_CHANNELS = 2
CHANNEL = 1
WAIT_TIME = 0.2
RESET_ON_START = True

if RESET_ON_START:
    reset_visa = VisaDriver(VISA_RESOURCE)
    reset_visa.open()
    reset_visa.write("*RST")
    reset_visa.query("*OPC?")
    reset_visa.write("*CLS")
    reset_visa.close()
    time.sleep(WAIT_TIME)

awg = InstroAWG(name="RigolAWG", driver=RigolDG1022Z(visa_resource=VISA_RESOURCE), num_channels=NUM_CHANNELS)
if DATASET_RID:
    awg.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

awg.open()
try:
    # ------------- Modulation tests ----------------
    awg.set_waveform(CHANNEL, Sine(frequency_hz=10_000.0))
    awg.set_amplitude(CHANNEL, 2.0, AmplitudeMeasurementUnit.VPP)
    awg.output_enable(CHANNEL, True)

    awg.set_waveform(CHANNEL, Pulse(frequency_hz=10_000.0, width_s=20e-6))
    awg.set_modulation(CHANNEL, ModulationType.PWM, Sine(frequency_hz=100.0), 5e-6)
    print("mod type (configured, not enabled):", awg.get_modulation_type(CHANNEL))
    time.sleep(WAIT_TIME)

    awg.modulation_enable(CHANNEL, True)
    print("mod enabled:", awg.get_modulation_state(CHANNEL).latest)
    time.sleep(WAIT_TIME)

    awg.modulation_enable(CHANNEL, False)

    awg.set_waveform(CHANNEL, Sine(frequency_hz=10_000.0))

    awg.set_modulation(CHANNEL, ModulationType.AM, Sine(frequency_hz=100.0), 80.0)
    print("mod type (configured, not enabled):", awg.get_modulation_type(CHANNEL))
    print("mod enabled:", awg.get_modulation_state(CHANNEL).latest)
    time.sleep(WAIT_TIME)

    awg.modulation_enable(CHANNEL, True)
    print("mod enabled:", awg.get_modulation_state(CHANNEL).latest)
    time.sleep(WAIT_TIME)

    awg.set_modulation(CHANNEL, ModulationType.FM, Sine(frequency_hz=100.0), 1_000.0)
    print("mod type:", awg.get_modulation_type(CHANNEL))
    time.sleep(WAIT_TIME)

    awg.set_modulation(CHANNEL, ModulationType.PM, Triangle(frequency_hz=50.0), 90.0)
    print("mod type:", awg.get_modulation_type(CHANNEL))
    time.sleep(WAIT_TIME)

    awg.set_modulation(CHANNEL, ModulationType.FSK, Square(frequency_hz=50.0), 20_000.0)
    print("mod type:", awg.get_modulation_type(CHANNEL))
    time.sleep(WAIT_TIME)

    awg.set_modulation(CHANNEL, ModulationType.PSK, Square(frequency_hz=50.0), 180.0)
    print("mod type:", awg.get_modulation_type(CHANNEL))
    time.sleep(WAIT_TIME)

    awg.set_modulation(CHANNEL, ModulationType.ASK, Square(frequency_hz=100.0), 1.0)
    print("mod type:", awg.get_modulation_type(CHANNEL))
    time.sleep(WAIT_TIME)

    awg.modulation_enable(CHANNEL, False)
    print("mod enabled:", awg.get_modulation_state(CHANNEL).latest)

    # ------------- Burst tests ------------------------
    awg.set_waveform(CHANNEL, Sine(frequency_hz=10_000.0))
    awg.set_amplitude(CHANNEL, 2.0, AmplitudeMeasurementUnit.VPP)

    awg.set_burst(CHANNEL, BurstType.NCYCLE)
    awg.set_burst_ncycles(CHANNEL, 5)
    awg.set_burst_period(CHANNEL, 10e-3)
    awg.set_burst_delay(CHANNEL, 0.0)
    awg.set_burst_trigger(CHANNEL, BurstTriggerSource.INTERNAL)
    print("burst type:", awg.get_burst_type(CHANNEL))
    print("ncycles:", awg.get_burst_ncycles(CHANNEL).latest)
    print("period:", awg.get_burst_period(CHANNEL).latest)
    print("delay:", awg.get_burst_delay(CHANNEL).latest)
    print("trigger:", awg.get_burst_trigger(CHANNEL))
    time.sleep(WAIT_TIME)

    awg.burst_enable(CHANNEL, True)
    print("burst enabled:", awg.get_burst_state(CHANNEL).latest)
    awg.set_burst_delay(CHANNEL, 1e-3)
    print("delay:", awg.get_burst_delay(CHANNEL).latest)
    time.sleep(WAIT_TIME)
    awg.set_burst_delay(CHANNEL, 0.0)

    awg.set_burst_trigger(CHANNEL, BurstTriggerSource.MANUAL)
    print("trigger:", awg.get_burst_trigger(CHANNEL))
    for i in range(3):
        awg.fire_burst_trigger(CHANNEL)
        print(f"fired manual burst {i + 1}")
        time.sleep(WAIT_TIME)

    awg.set_burst(CHANNEL, BurstType.INFINITE)
    awg.set_burst_trigger(CHANNEL, BurstTriggerSource.MANUAL)
    print("burst type:", awg.get_burst_type(CHANNEL))
    awg.fire_burst_trigger(CHANNEL)
    time.sleep(WAIT_TIME)

    awg.set_burst(CHANNEL, BurstType.GATED)
    print("burst type:", awg.get_burst_type(CHANNEL))
    awg.set_burst_gate_polarity(CHANNEL, GatePolarity.NORM)
    print("gate polarity:", awg.get_burst_gate_polarity(CHANNEL))
    time.sleep(WAIT_TIME)
    awg.set_burst_gate_polarity(CHANNEL, GatePolarity.INV)
    print("gate polarity:", awg.get_burst_gate_polarity(CHANNEL))
    time.sleep(WAIT_TIME)

    awg.burst_enable(CHANNEL, False)
    print("burst enabled:", awg.get_burst_state(CHANNEL).latest)

    # ------------- Sweep tests -----------------------
    awg.set_waveform(CHANNEL, Sine(frequency_hz=1_000.0))
    awg.set_sweep(CHANNEL, SweepType.LINEAR)
    awg.set_sweep_start_freq(CHANNEL, 1_000.0)
    awg.set_sweep_end_freq(CHANNEL, 10_000.0)
    awg.set_sweep_time(CHANNEL, 1.0)
    awg.set_sweep_start_hold_time(CHANNEL, 0.1)
    awg.set_sweep_stop_hold_time(CHANNEL, 0.1)
    awg.set_sweep_return_time(CHANNEL, 0.2)
    awg.set_sweep_trigger(CHANNEL, SweepTriggerSource.INTERNAL)
    awg.sweep_enable(CHANNEL, True)

    print("sweep type:", awg.get_sweep_type(CHANNEL))
    print("sweep enabled:", awg.get_sweep_state(CHANNEL).latest)
    print("start Hz:", awg.get_sweep_start_freq(CHANNEL).latest)
    print("end Hz:", awg.get_sweep_end_freq(CHANNEL).latest)
    print("sweep time:", awg.get_sweep_time(CHANNEL).latest)
    print("start hold:", awg.get_sweep_start_hold_time(CHANNEL).latest)
    print("stop hold:", awg.get_sweep_stop_hold_time(CHANNEL).latest)
    print("return time:", awg.get_sweep_return_time(CHANNEL).latest)
    print("sweep trigger:", awg.get_sweep_trigger(CHANNEL))
    time.sleep(WAIT_TIME)

    for sweep_type in (SweepType.LOG, SweepType.STEP):
        awg.set_sweep(CHANNEL, sweep_type)
        print("sweep type:", awg.get_sweep_type(CHANNEL))
        time.sleep(WAIT_TIME)

    awg.set_sweep(CHANNEL, SweepType.LINEAR)
    awg.set_sweep_trigger(CHANNEL, SweepTriggerSource.MANUAL)
    print("sweep trigger:", awg.get_sweep_trigger(CHANNEL))
    for i in range(3):
        awg.fire_sweep_trigger(CHANNEL)
        print(f"fired manual sweep {i + 1}")
        time.sleep(WAIT_TIME)

    awg.sweep_enable(CHANNEL, False)
    print("sweep enabled:", awg.get_sweep_state(CHANNEL).latest)
finally:
    for ch in range(1, NUM_CHANNELS + 1):
        awg.output_enable(ch, False)
    awg.close()
