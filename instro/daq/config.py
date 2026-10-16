"""DAQ driver registry.

DAQs have no JSON config schema yet; this module exists so :func:`instro.lib.registry.driver_registry`
and discovery can resolve DAQ drivers by name. The vendor-package drivers are registered here with
import paths into their packages: the name resolves when the package is installed and raises
``ModuleNotFoundError`` otherwise. They carry no ``*IDN?`` pattern because their devices are found
through the vendor SDK's own enumeration, not SCPI.
"""

from __future__ import annotations

from instro.lib.registry import DriverEntry, IdnPattern

DAQ_VENDOR_REGISTRY: dict[str, DriverEntry] = {
    "Keysight34980A": DriverEntry(
        "instro.daq.drivers.keysight_34980a.Keysight34980A",
        (IdnPattern(("KEYSIGHT TECHNOLOGIES", "AGILENT TECHNOLOGIES"), r"^34980A"),),
    ),
    "NIDAQDriver": DriverEntry("instro.daq.drivers.ni.nidaq.NIDAQDriver"),
    "LabJackTSeriesDriver": DriverEntry("instro.daq.drivers.labjack.t_series.LabJackTSeriesDriver"),
    "MCCDriver": DriverEntry("instro.daq.drivers.mcc.mccdaq.MCCDriver"),
}
