"""Every concrete instrument driver in one namespace.

The drivers ship in instro itself and in the optional vendor packages::

    from instro.drivers import BK9115, Keysight34461A, VirtualELoad

The per-category paths (``instro.psu.drivers``, ``instro.dmm.drivers``, ...) still work and export the same
classes. Drivers from vendor packages (``NIDAQDriver``, ``LabJackTSeriesDriver``, ``MCCDriver``,
``Aardvark``) load on first access and raise `ImportError` naming the extra to install when their package
is missing.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from instro.awg.drivers import AWGDriverBase, Keysight33521B, RigolDG1022Z
from instro.daq.drivers import DAQDriverBase, HWTimestamper, Keysight34980A
from instro.dmm.drivers import (
    Agilent34401A,
    DMMDriverBase,
    Keithley2400,
    Keysight34461A,
    SimulatedDMM,
    VirtualDMM,
)
from instro.eload.drivers import BK85XXB, ELoadDriverBase, RigolDL30xx, VirtualELoad
from instro.i2c.drivers import I2CDriverBase
from instro.psu.drivers import (
    BK914X,
    BK9115,
    EAPSB10000Visa,
    GWInstekGPD3303,
    KeysightE36100,
    KeysightN5700,
    PSUDriverBase,
    RigolDP800,
    SiglentSPD3303,
    SimulatedPSU,
    TDKLambdaGenesys,
    VirtualPSU,
)
from instro.scope.drivers import (
    Keysight1200X,
    ScopeDriverBase,
    SiglentSDS1000XE,
    Tektronix2SeriesMSO,
    VirtualScope,
)

if TYPE_CHECKING:
    from instro.daq.drivers.labjack import LabJackTSeriesDriver as LabJackTSeriesDriver
    from instro.daq.drivers.mcc import MCCDriver as MCCDriver
    from instro.daq.drivers.ni import NIDAQDriver as NIDAQDriver
    from instro.i2c.drivers.totalphase import Aardvark as Aardvark

# Vendor-package driver -> (module, extra that installs it).
_VENDOR_DRIVERS: dict[str, tuple[str, str]] = {
    "Aardvark": ("instro.i2c.drivers.totalphase", "aardvark"),
    "LabJackTSeriesDriver": ("instro.daq.drivers.labjack", "labjack"),
    "MCCDriver": ("instro.daq.drivers.mcc", "mccdaq"),
    "NIDAQDriver": ("instro.daq.drivers.ni", "nidaq"),
}

__all__ = [
    # Abstract driver bases
    "AWGDriverBase",
    "DAQDriverBase",
    "DMMDriverBase",
    "ELoadDriverBase",
    "HWTimestamper",
    "I2CDriverBase",
    "PSUDriverBase",
    "ScopeDriverBase",
    # AWG
    "Keysight33521B",
    "RigolDG1022Z",
    # DAQ
    "Keysight34980A",
    "LabJackTSeriesDriver",
    "MCCDriver",
    "NIDAQDriver",
    # DMM
    "Agilent34401A",
    "Keithley2400",
    "Keysight34461A",
    "SimulatedDMM",
    "VirtualDMM",
    # E-Load
    "BK85XXB",
    "RigolDL30xx",
    "VirtualELoad",
    # I2C
    "Aardvark",
    # PSU (EAPSB10000Visa is bidirectional: it also backs the E-Load category)
    "BK914X",
    "BK9115",
    "EAPSB10000Visa",
    "GWInstekGPD3303",
    "KeysightE36100",
    "KeysightN5700",
    "RigolDP800",
    "SiglentSPD3303",
    "SimulatedPSU",
    "TDKLambdaGenesys",
    "VirtualPSU",
    # Scope
    "Keysight1200X",
    "SiglentSDS1000XE",
    "Tektronix2SeriesMSO",
    "VirtualScope",
]


def __getattr__(name: str) -> Any:
    if name not in _VENDOR_DRIVERS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, extra = _VENDOR_DRIVERS[name]
    try:
        value = getattr(importlib.import_module(module), name)
    except ModuleNotFoundError as exc:
        if exc.name is None or not module.startswith(exc.name):
            raise
        raise ImportError(f"{name} needs the {extra!r} extra: pip install 'instro[{extra}]'") from exc
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
