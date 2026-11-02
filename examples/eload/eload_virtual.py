"""Example: Drive a virtual E-Load, with no hardware or server.

VirtualELoad simulates the load and the source it draws from in-process, so this runs as-is.
Swap in a real driver (BK85XXB, RigolDL30xx, ...) and the rest is unchanged.
"""

from instro.eload import InstroELoad, LoadMode
from instro.eload.drivers import VirtualELoad

driver = VirtualELoad(source_voltage=12.0, source_resistance=0.5)  # optional: the simulated source

with InstroELoad(name="myELoad", driver=driver) as eload:
    eload.set_mode(LoadMode.CC)
    eload.set_level(2.0)
    eload.output_enable(True)
    print(f"CC 2 A:   {eload.get_voltage().latest:.3f} V, {eload.get_current().latest:.3f} A")

    eload.set_mode(LoadMode.CP)
    eload.set_level(30.0)
    print(f"CP 30 W:  {eload.get_voltage().latest:.3f} V, {eload.get_current().latest:.3f} A")

    eload.output_enable(False)
