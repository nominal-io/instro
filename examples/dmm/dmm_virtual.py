"""Example: Read a virtual DMM, with no hardware or server.

VirtualDMM simulates readings in-process, so this runs as-is. Swap in a real
driver (Agilent34401A, Keysight34461A, ...) and the rest is unchanged.
"""

from instro.dmm import InstroDMM, MeasurementFunction
from instro.dmm.drivers import VirtualDMM

driver = VirtualDMM({MeasurementFunction.DC_VOLTAGE: 3.3})  # optional: what each function reads

with InstroDMM(name="myDMM", driver=driver) as dmm:
    print(f"DC voltage: {dmm.read_dc_voltage().latest:.4f} V")
    print(f"Resistance: {dmm.read_resistance().latest:.1f} ohm")
