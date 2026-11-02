"""Example: Discharge a virtual battery with a virtual E-Load, with no hardware or server.

VirtualELoad simulates the load and the source it draws from in-process, so this runs as-is.
The source's voltage and internal resistance are attributes you can change mid-run; here they
follow a cell's state of charge. Swap in a real driver (BK85XXB, RigolDL30xx, ...) and a real
battery, drop the source updates, and the rest is unchanged.
"""

from instro.eload import InstroELoad, LoadMode
from instro.eload.drivers import VirtualELoad

source = VirtualELoad(source_voltage=4.2, source_resistance=0.1)  # a full Li-ion cell

with InstroELoad(name="myELoad", driver=source) as eload:
    eload.set_mode(LoadMode.CC)
    eload.set_level(1.0)
    eload.output_enable(True)
    for soc in (1.0, 0.5, 0.0):
        source.source_voltage = 3.0 + 1.2 * soc  # open-circuit voltage falls as the cell drains
        source.source_resistance = 0.1 + 0.2 * (1 - soc)  # internal resistance rises
        print(f"SoC {soc:.0%}: {eload.get_voltage().latest:.3f} V at {eload.get_current().latest:.3f} A")
    eload.output_enable(False)
