"""Example: Basic oscilloscope usage.

Demonstrates connecting to an oscilloscope, configuring channel and trigger
settings, capturing a waveform, and taking built-in measurements.

"""

import time

from instro.lib.publishers import NominalCorePublisher
from instro.lib.transports.visa import VisaConfig
from instro.unstable.scope import (
    AcquisitionMode,
    Coupling,
    InstroScope,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerType,
)
from instro.unstable.scope.drivers.keysight import Keysight1200X

# VISA_RESOURCE = "TCPIP0::127.0.0.1::INSTR"
# VISA_RESOURCE = "USB0::0x2A8D::0x039B::CN64191203::INSTR"
VISA_RESOURCE = "USB0::10893::923::CN64191203::INSTR"
DATASET_RID = (
    "ri.catalog.cerulean-staging.dataset.50a647a4-0d00-460c-9898-4c282adfe7a4"  # Replace with your dataset RID.
)

scope = InstroScope(
    name="scope",
    driver=Keysight1200X(VisaConfig(visa_resource=VISA_RESOURCE, visa_backend="@py")),
    num_channels=4,
    publishers=[NominalCorePublisher(dataset_rid=DATASET_RID)],
)

try:
    scope.open()

    # # --- Configure channel 1 ---
    scope.set_coupling(Coupling.DC, channel=1)
    scope.set_probe_attenuation(1.0, channel=1)
    scope.set_vertical_scale(1, channel=1)  # 1 V/div
    scope.set_vertical_offset(0, channel=1)

    # # --- Configure timebase ---
    scope.set_horizontal_scale(0.01)  # 100 ms/div

    # # --- Configure acquisition ---
    scope.set_acquisition_mode(AcquisitionMode.NORMAL)

    # # # --- Configure trigger ---
    scope.set_trigger_source(channel=1)

    scope.set_trigger_type(TriggerType.EDGE)
    scope.set_trigger_slope(TriggerSlope.RISING)
    scope.set_trigger_level(0)  # 2.5 Volts
    scope.set_trigger_mode(TriggerMode.NORMAL)

    # # # --- Acquire and fetch ---
    scope.single()
    time.sleep(2)  # Wait for trigger and acquisition

    waveform = scope.fetch_waveform(channel=1)
    print(f"Waveform captured: {len(waveform.channel_data)} channel(s)")

    # # # --- Built-in measurements ---
    vpp = scope.measure(ScopeMeasurementType.VPP, channel=1)
    vrms = scope.measure(ScopeMeasurementType.VRMS, channel=1, timeout=10)
    vmax = scope.measure(ScopeMeasurementType.VMAX, channel=1)
    freq = scope.measure(ScopeMeasurementType.FREQUENCY, 1)
    per = scope.measure(ScopeMeasurementType.PERIOD, 1)
    vmin = scope.measure(ScopeMeasurementType.VMIN, 1)
    print(f"Vpp:  {vpp}")
    print(f"Vrms: {vrms}")
    print(f"Vmax: {vmax}")
    print(f"Vpp:  {vmin}")
    print(f"Vrms: {freq}")
    print(f"Vmax: {per}")

    # # --- Read back settings ---
    sample_rate = scope.get_sample_rate()
    print(f"Sample rate: {sample_rate}")

    # # # --- Save a screenshot ---
    scope.save_screenshot("capture.png")

finally:
    scope.close()
