"""Example: Oscilloscope background daemon.

Demonstrates using InstroScope's background daemon to continuously measure
Vrms, Vpp, and Frequency on channel 1 while printing results in a main loop.

"""

from instro.lib.publishers import NominalCorePublisher
from instro.unstable.scope import (
    InstroScope,
    ScopeMeasurementType,
)
from instro.unstable.scope.drivers.keysight import Keysight1200X
from instro.unstable.scope.types import AcquisitionMode, Coupling, TriggerMode, TriggerSlope, TriggerType

VISA_RESOURCE = "USB0::10893::923::CN64191203::INSTR"
DATASET_RID = "ri.catalog.cerulean-staging.dataset.50a647a4-0d00-460c-9898-4c282adfe7a4"

scope = InstroScope(
    name="scope",
    driver=Keysight1200X(VISA_RESOURCE),
    num_channels=2,
    publishers=[NominalCorePublisher(DATASET_RID)],
)

try:
    scope.open()

    # # --- Configure channel 1 ---
    scope.set_coupling(Coupling.DC, channel=1)
    scope.set_probe_attenuation(1.0, channel=1)
    scope.set_vertical_scale(1.0, channel=1)  # 1 V/div
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

    # --- Register background measurements ---
    scope.add_background_daemon_function(scope.measure, ScopeMeasurementType.VRMS, channel=1)
    scope.add_background_daemon_function(scope.measure, ScopeMeasurementType.VPP, channel=1)
    scope.add_background_daemon_function(scope.measure, ScopeMeasurementType.FREQUENCY, channel=1)

    # --- Start continuous acquisition on the instrument ---
    scope.run()

    # --- Start the background daemon ---
    scope.start()

    # --- Main loop: read back measurements and print ---
    while True:
        try:
            vrms = scope.get_channel("scope.ch1.vrms", length=1, wait_for_new_samples=True)
            vpp = scope.get_channel("scope.ch1.vpp", length=1)
            freq = scope.get_channel("scope.ch1.frequency", length=1)

            print(f"Vrms: {vrms.latest}  Vpp: {vpp.latest}  Frequency: {freq.latest}")

        except KeyboardInterrupt:
            break

    print("Done")

finally:
    scope.close()
