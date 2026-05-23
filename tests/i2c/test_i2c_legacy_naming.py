"""Unit tests for I2CInterface channel naming, including the legacy_naming flag."""

from unittest.mock import MagicMock

import pytest

from instro.i2c import I2CDriverBase, I2CInterface
from instro.i2c.types import DataFormat, FieldDef, RegisterDef, RegisterDevice, SystemDefinition


def _make_system_definition() -> SystemDefinition:
    """Build a minimal SystemDefinition with one register device exposing one register + one field."""
    reg = RegisterDef(
        alias="status",
        register=0x10,
        format=DataFormat(transfer_bits=8),
        fields={"ready": FieldDef(name="ready", lsb=0, width_bits=1)},
    )
    device = RegisterDevice(name="gpio", address=0x20, registers={"status": reg})
    system = SystemDefinition()
    system.devices = {"gpio": device}
    return system


def _stub_driver() -> MagicMock:
    """Build a stub I2CDriverBase that returns a single byte 0x01 from any read."""
    driver = MagicMock(spec=I2CDriverBase)
    driver.write_read.return_value = bytes([0x01])
    driver.read.return_value = bytes([0x01])
    return driver


def test_default_naming_publishes_dot_separated_channel_keys() -> None:
    """Default (`legacy_naming=False`) publishes `{name}.{periph}.{register}` for reads."""
    i2c = I2CInterface(name="ut", driver=_stub_driver(), system_definition=_make_system_definition())
    measurement = i2c.read("gpio", "status")
    assert "ut.gpio.status" in measurement.channel_data


def test_default_naming_publishes_dot_cmd_suffix_for_writes() -> None:
    """Default writes publish `{name}.{periph}.{register}.cmd`."""
    i2c = I2CInterface(name="ut", driver=_stub_driver(), system_definition=_make_system_definition())
    command = i2c.write("gpio", "status", 0x01)
    assert "ut.gpio.status.cmd" in command.channel_data


def test_default_naming_field_read_uses_dot_separator() -> None:
    """Default field reads publish `{name}.{periph}.{register}.{field}`."""
    i2c = I2CInterface(name="ut", driver=_stub_driver(), system_definition=_make_system_definition())
    measurement = i2c.read("gpio", "status", field="ready")
    assert "ut.gpio.status.ready" in measurement.channel_data


def test_legacy_naming_publishes_underscore_separated_channel_keys() -> None:
    """`legacy_naming=True` restores pre-v1.0 underscore-separator form for reads — including the name separator."""
    i2c = I2CInterface(
        name="ut", driver=_stub_driver(), system_definition=_make_system_definition(), legacy_naming=True
    )
    measurement = i2c.read("gpio", "status")
    assert "ut_gpio_status" in measurement.channel_data
    assert "ut.gpio.status" not in measurement.channel_data
    assert "ut.gpio_status" not in measurement.channel_data  # no hybrid form


def test_legacy_naming_uses_underscore_cmd_suffix_for_writes() -> None:
    """`legacy_naming=True` writes publish `{name}_{periph}_{register}_cmd` — fully underscore-separated."""
    i2c = I2CInterface(
        name="ut", driver=_stub_driver(), system_definition=_make_system_definition(), legacy_naming=True
    )
    command = i2c.write("gpio", "status", 0x01)
    assert "ut_gpio_status_cmd" in command.channel_data
    assert "ut.gpio_status.cmd" not in command.channel_data
    assert "ut.gpio_status_cmd" not in command.channel_data  # no hybrid form


def test_legacy_naming_field_read_keeps_underscore_separator() -> None:
    """Field reads under `legacy_naming` separate every part with underscore, including the name."""
    i2c = I2CInterface(
        name="ut", driver=_stub_driver(), system_definition=_make_system_definition(), legacy_naming=True
    )
    measurement = i2c.read("gpio", "status", field="ready")
    assert "ut_gpio_status_ready" in measurement.channel_data
    assert "ut.gpio_status_ready" not in measurement.channel_data  # no hybrid form


def test_legacy_naming_default_is_false() -> None:
    """The flag defaults to False (new naming) when not supplied."""
    i2c = I2CInterface(name="ut", driver=_stub_driver(), system_definition=_make_system_definition())
    assert i2c.legacy_naming is False
