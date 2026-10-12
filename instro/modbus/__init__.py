"""Modbus protocol support (experimental). All public names re-exported here — no submodule imports needed."""

from instro.lib.types import DeviceInfo, LinearScale
from instro.modbus.modbus import ModbusDevice
from instro.modbus.types import (
    BitDef,
    ModbusConfig,
    RegisterDef,
    TimingConfig,
)

__all__ = [
    "ModbusDevice",
    "ModbusConfig",
    "TimingConfig",
    "RegisterDef",
    "BitDef",
    "DeviceInfo",
    "LinearScale",
]
