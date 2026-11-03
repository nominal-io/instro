"""Example: Capture from a virtual oscilloscope, with no hardware or server.

VirtualScope simulates waveforms in-process, so this runs as-is: channel 1 sees a 1 kHz sine and
channel 2 a 1 kHz square. Swap in a real driver (Keysight1200X, SiglentSDS1000XE, ...) and the
rest is unchanged.
"""

from instro.scope import InstroScope, ScopeMeasurementType
from instro.scope.drivers import VirtualScope

driver = VirtualScope(num_channels=2)  # optional: signals={1: SineWave(frequency=5e3), ...}

with InstroScope(name="myScope", driver=driver, num_channels=2) as scope:
    scope.set_horizontal_scale(200e-6)  # 200 us/div: two cycles across the screen
    scope.set_trigger_source(1)
    scope.set_trigger_level(0.0)
    scope.single()

    waveform = scope.fetch_waveform(channel=1)
    print(f"Captured {len(waveform.timestamps)} points per channel")
    for channel in (1, 2):
        vpp = scope.measure(ScopeMeasurementType.VPP, channel=channel).latest
        vrms = scope.measure(ScopeMeasurementType.VRMS, channel=channel).latest
        frequency = scope.measure(ScopeMeasurementType.FREQUENCY, channel=channel).latest
        print(f"ch{channel}: {vpp:.3f} Vpp, {vrms:.3f} Vrms @ {frequency:.0f} Hz")
