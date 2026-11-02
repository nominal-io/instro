"""Tests for the SDK-free VirtualI2C driver."""

from __future__ import annotations

import pytest

from instro.i2c import I2CInterface
from instro.i2c.drivers import VirtualI2C
from instro.i2c.types import DataFormat, FieldDef, RegisterDef, RegisterDevice, SystemDefinition


def _system() -> SystemDefinition:
    status = RegisterDef(
        alias="status",
        register=0x11,
        default_value=0x5A,
        fields={"ready": FieldDef(name="ready", lsb=0)},
    )
    config = RegisterDef(alias="config", register=0x10, format=DataFormat(transfer_bits=16), endianness="little")
    eeprom = RegisterDef(alias="cell", register=0x0102)
    gpio = RegisterDevice(name="gpio", address=0x20, registers={"status": status, "config": config})
    rom = RegisterDevice(name="rom", address=0x50, addr_width_bytes=2, registers={"cell": eeprom})
    return SystemDefinition(devices={gpio.name: gpio, rom.name: rom})


def test_register_and_field_writes_read_back() -> None:
    i2c = I2CInterface(name="i2c", driver=VirtualI2C(addr_width_bytes={0x50: 2}), system_definition=_system())
    with i2c:
        i2c.write("gpio", "status", 0xA0)
        i2c.write("gpio", "config", 0x1234)
        i2c.write("gpio", "status", 1, field="ready")
        i2c.write("rom", "cell", 0xAB)
        assert i2c.read("gpio", "status").latest == 0xA1
        assert i2c.read("gpio", "status", field="ready").latest == 1
        assert i2c.read("gpio", "config").latest == 0x1234
        assert i2c.read("rom", "cell").latest == 0xAB


def test_reset_reg_restores_default_over_initial_contents() -> None:
    i2c = I2CInterface(name="i2c", driver=VirtualI2C({0x20: {0x11: b"\xff"}}), system_definition=_system())
    with i2c:
        assert i2c.read("gpio", "status").latest == 0xFF
        i2c.reset_reg("gpio", "status")
        assert i2c.read("gpio", "status").latest == 0x5A


def test_unlisted_address_naks() -> None:
    i2c = I2CInterface(name="i2c", driver=VirtualI2C({0x20: {}}), system_definition=_system())
    with i2c, pytest.raises(OSError, match="0x50"):
        i2c.read("rom", "cell")
