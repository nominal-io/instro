"""Example: Drive a virtual PSU, with no hardware or server.

VirtualPSU simulates its outputs in-process, so this runs as-is. Swap in a real
driver (BK9115, RigolDP800, ...) and the rest is unchanged.
"""

from instro import InstroPSU
from instro.drivers import VirtualPSU

driver = VirtualPSU(num_channels=1, load_resistance=10.0)  # optional: channel count and load in ohms

with InstroPSU(name="myPSU", driver=driver, num_channels=1) as psu:
    psu.apply(current_limit=1.0, voltage=5.0, enable=True, channel=1)
    print(f"Voltage: {psu.get_voltage(channel=1).latest:.3f} V")
    print(f"Current: {psu.get_current(channel=1).latest:.3f} A")
    print(f"Mode:    {psu.get_operating_mode(channel=1).latest}")

    psu.set_current_limit(0.2, channel=1)  # the 10 ohm load now wants more than the limit
    print(f"Voltage: {psu.get_voltage(channel=1).latest:.3f} V")
    print(f"Mode:    {psu.get_operating_mode(channel=1).latest}")
