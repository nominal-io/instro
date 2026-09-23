"""Example: Basic arbitrary waveform generator usage.

Demonstrates connecting to an AWG, programming a sine wave on channel 1,
setting its amplitude and offset, enabling the output, and reading the
settings back.

"""

from instro.awg import AmplitudeMeasurementUnit, InstroAWG, Sine, Square
from instro.awg.drivers import RigolDG1022Z
from instro.lib.publishers import NominalCorePublisher
from instro.lib.transports import VisaConfig

VISA_RESOURCE = "<visa_resource>"  # Replace with your instrument's VISA resource string.
DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.

awg = InstroAWG(
    name="awg",
    driver=RigolDG1022Z(VisaConfig(visa_resource=VISA_RESOURCE)),
    num_channels=2,
    publishers=[NominalCorePublisher(dataset_rid=DATASET_RID)],
)

with awg:
    # --- Program a 1 kHz sine on channel 1 ---
    awg.set_waveform(1, Sine(frequency_hz=1000.0))
    awg.set_amplitude(1, 2.0, AmplitudeMeasurementUnit.VPP)
    awg.set_offset(1, 0.0)

    # Match the load the output actually drives, so the instrument reports the
    # voltage present at the device under test. Use None for high-Z.
    awg.set_output_load(1, 50.0)

    awg.output_enable(1, True)

    # --- Read the settings back ---
    waveform = awg.get_waveform(1)
    amplitude, unit = awg.get_amplitude(1)
    offset = awg.get_offset(1)
    enabled = awg.get_output_state(1)
    print(f"Waveform:  {waveform}")
    print(f"Amplitude: {amplitude} {unit.value}")
    print(f"Offset:    {offset.latest} V")
    print(f"Output on: {enabled.latest}")

    # --- Switch channel 1 to a 25% duty-cycle square wave ---
    awg.set_waveform(1, Square(frequency_hz=1000.0, duty_cycle_pct=25.0))

    awg.output_enable(1, False)
