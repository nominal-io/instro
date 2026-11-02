"""Example: Program a virtual AWG, with no hardware or server.

VirtualAWG holds channel settings in-process, so this runs as-is. Swap in a real
driver (RigolDG1022Z, Keysight33521B, ...) and the rest is unchanged.
"""

from instro.awg import AmplitudeMeasurementUnit, InstroAWG, Sine
from instro.awg.drivers import VirtualAWG

with InstroAWG(name="myAWG", driver=VirtualAWG(num_channels=2), num_channels=2) as awg:
    awg.set_waveform(1, Sine(frequency_hz=5000.0))
    awg.set_amplitude(1, 2.0, AmplitudeMeasurementUnit.VPP)
    awg.output_enable(1, True)

    amplitude, unit = awg.get_amplitude(1)
    print(f"Waveform:  {awg.get_waveform(1)}")
    print(f"Amplitude: {amplitude} {unit.value}")
    print(f"Output on: {bool(awg.get_output_state(1).latest)}")
