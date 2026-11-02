"""Example: Capture from a virtual oscilloscope, with no hardware or server.

VirtualScope simulates waveforms in-process, so this runs as-is. Swap in a real
driver (Keysight1200X, SiglentSDS1000XE, ...) and the rest is unchanged.
"""

from instro.scope import InstroScope, ScopeMeasurementType
from instro.scope.drivers import VirtualScope
from instro.scope.drivers.virtual import SineWave

driver = VirtualScope({2: SineWave(frequency=5e3, amplitude=0.5)})  # optional: what each channel sees

with InstroScope(name="myScope", driver=driver, num_channels=2) as scope:
    scope.set_horizontal_scale(200e-6)  # 200 us/div
    scope.set_trigger_source(1)
    scope.set_trigger_level(0.0)
    scope.single()

    waveform = scope.fetch_waveform(channel=1)
    print(f"Captured {len(waveform.timestamps)} points on ch1")
    for channel in (1, 2):
        vpp = scope.measure(ScopeMeasurementType.VPP, channel=channel).latest
        frequency = scope.measure(ScopeMeasurementType.FREQUENCY, channel=channel).latest
        print(f"ch{channel}: {vpp:.3f} Vpp @ {frequency:.0f} Hz")
