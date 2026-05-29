"""Concrete register drivers."""

from instro.register.drivers.modbus import (
    BitDef,
    ModbusConfig,
    ModbusRegisterDef,
    ModbusRegisterDriver,
    TimingConfig,
)

RegisterDef = ModbusRegisterDef  # backwards-compatible alias

__all__ = ["ModbusRegisterDriver", "ModbusConfig", "ModbusRegisterDef", "RegisterDef", "BitDef", "TimingConfig"]
