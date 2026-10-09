"""Unit tests for the I2C JSON config.

Covers ``I2CConfig`` validation, the ``SystemDefinition`` it builds, and ``I2CInterface(config=...)``
construction, polling, and runtime scaling. Pydantic field defaults and simple constraints are not
tested; only the custom validators and the config-to-runtime conversion are.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from instro.i2c import I2CConfig, I2CDriverBase, I2CInterface
from instro.i2c.types import (
    CommandDevice,
    CustomScaling,
    DataFormat,
    LinearScaling,
    RegisterDef,
    RegisterDevice,
    SystemDefinition,
)

CONFIG_PATH = Path(__file__).parent / "configs" / "sensor_bus.json"


def _stub_driver() -> MagicMock:
    """Stub I2CDriverBase returning 0x0120 (two bytes) from any read."""
    driver = MagicMock(spec=I2CDriverBase)
    driver.write_read.return_value = bytes([0x01, 0x20])
    driver.read.return_value = bytes([0x01, 0x20])
    return driver


def _minimal_config(**overrides) -> dict:
    """Smallest valid config dict with one register device; ``overrides`` replace top-level keys."""
    config = {
        "device": {"name": "bus"},
        "devices": [
            {
                "type": "register",
                "name": "gpio",
                "address": 0x20,
                "registers": [{"alias": "status", "register": 0x10}],
            }
        ],
    }
    config.update(overrides)
    return config


# ============ Loading and conversion ============


def test_from_json_builds_matching_system_definition() -> None:
    """Hex strings, fields, scaling, and command enums round-trip from JSON into the runtime dataclasses."""
    config = I2CConfig.from_json(CONFIG_PATH)
    sysdef = config.build_system_definition()

    gpio = sysdef.device("power_gpio")
    assert isinstance(gpio, RegisterDevice)
    assert gpio.address == 0x21
    led = gpio.register("LED_OUTPUT_STATE")
    assert led.register == 0x0C
    assert led.format.transfer_bits == 8
    assert led.field("led_2").mask() == 0b10
    assert gpio.register("LED_DIRECTION").format == DataFormat(transfer_bits=8)

    adc = sysdef.device("VOLTAGE_ADC")
    assert isinstance(adc, CommandDevice)
    assert adc.address == 0x09
    assert isinstance(adc.data_format.scaling, LinearScaling)
    assert adc.data_format.scaling.gain == pytest.approx(0.0087912088)
    assert adc.data_format.float_from_raw(0x0100) == pytest.approx(16 * 0.0087912088)
    assert adc.command("channel").values["CH1"].value == 0xC0
    assert sum(member.value for member in adc.batch_commands["ch0"]) == 0x80 | 0x08 | 0x00


def test_from_json_string_path_and_missing_file() -> None:
    assert I2CConfig.from_json(str(CONFIG_PATH)).device.name == "sensor_bus"
    with pytest.raises(FileNotFoundError):
        I2CConfig.from_json("/nonexistent/bus.json")


@pytest.mark.parametrize("bad", ["0xZZ", "0x80", True])
def test_invalid_address_literals_rejected(bad) -> None:
    """Malformed hex, out-of-range 7-bit addresses, and bools are all rejected."""
    with pytest.raises(ValidationError):
        I2CConfig.model_validate(_minimal_config(devices=[{"type": "register", "name": "g", "address": bad}]))


def test_wrong_protocol_and_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError, match="protocol"):
        I2CConfig.model_validate(_minimal_config(protocol="modbus"))
    with pytest.raises(ValidationError, match="extra_forbidden"):
        I2CConfig.model_validate(_minimal_config(system_definition={}))


def test_unknown_connection_interface_rejected() -> None:
    with pytest.raises(ValidationError, match="'aardvark'"):
        I2CConfig.model_validate(_minimal_config(connection={"interface": "linux", "bus": 1}))


# ============ Cross-field validation ============


@pytest.mark.parametrize(
    ("devices", "match"),
    [
        (
            [
                {"type": "register", "name": "a", "address": 0x20},
                {"type": "register", "name": "a", "address": 0x21},
            ],
            "duplicate device name",
        ),
        (
            [
                {"type": "register", "name": "a", "address": 0x20},
                {"type": "command", "name": "b", "address": 0x20, "data_format": {"transfer_bits": 8}},
            ],
            "share I2C address",
        ),
        (
            [
                {
                    "type": "register",
                    "name": "a",
                    "address": 0x20,
                    "registers": [{"alias": "r", "register": 0}, {"alias": "r", "register": 1}],
                }
            ],
            "duplicate register alias",
        ),
        (
            [{"type": "register", "name": "a", "address": 0x20, "registers": [{"alias": "r", "register": 0x100}]}],
            "does not fit in addr_width_bytes=1",
        ),
        (
            [
                {
                    "type": "register",
                    "name": "a",
                    "address": 0x20,
                    "registers": [{"alias": "r", "register": 0, "default_value": 0x100}],
                }
            ],
            "default_value 0x100 does not fit",
        ),
        (
            [
                {
                    "type": "register",
                    "name": "a",
                    "address": 0x20,
                    "registers": [{"alias": "r", "register": 0, "fields": [{"name": "f", "lsb": 7, "width_bits": 2}]}],
                }
            ],
            "exceeds 8 transfer bits",
        ),
        (
            [
                {
                    "type": "register",
                    "name": "a",
                    "address": 0x20,
                    "registers": [
                        {"alias": "r", "register": 0, "fields": [{"name": "f", "lsb": 0}, {"name": "f", "lsb": 1}]}
                    ],
                }
            ],
            "duplicate field name",
        ),
        (
            [
                {
                    "type": "command",
                    "name": "adc",
                    "address": 0x48,
                    "data_format": {"transfer_bits": 16, "data_width_bits": 12, "data_lsb": 8},
                }
            ],
            "exceeds transfer_bits",
        ),
        (
            [
                {
                    "type": "command",
                    "name": "adc",
                    "address": 0x48,
                    "data_format": {"transfer_bits": 8},
                    "commands": {"channel": {"CH0": 0x100}},
                }
            ],
            "does not fit in one byte",
        ),
        (
            [
                {
                    "type": "command",
                    "name": "adc",
                    "address": 0x48,
                    "data_format": {"transfer_bits": 8},
                    "commands": {"channel": {"CH0": 0x80}},
                    "batch_commands": [{"name": "b", "commands": {"channel": "CH9"}}],
                }
            ],
            "unknown member 'CH9'",
        ),
        (
            [
                {
                    "type": "command",
                    "name": "adc",
                    "address": 0x48,
                    "data_format": {"transfer_bits": 8},
                    "batch_commands": [{"name": "b", "commands": {"mode": "X"}}],
                }
            ],
            "unknown command group 'mode'",
        ),
    ],
)
def test_cross_field_validation_rejects(devices, match) -> None:
    with pytest.raises(ValidationError, match=match):
        I2CConfig.model_validate(_minimal_config(devices=devices))


# ============ Connection block ============


def test_connection_build_returns_aardvark_with_serial() -> None:
    totalphase = pytest.importorskip("instro.i2c.drivers.totalphase")
    config = I2CConfig.from_json(CONFIG_PATH)
    assert config.connection is not None
    driver = config.connection.build()
    assert isinstance(driver, totalphase.Aardvark)
    assert driver._serial_number == "2239-764425"


def test_connection_build_without_vendor_package_points_at_extra() -> None:
    config = I2CConfig.from_json(CONFIG_PATH)
    assert config.connection is not None
    with patch.dict(sys.modules, {"instro.i2c.drivers.totalphase": None}):
        with pytest.raises(ImportError, match=r"instro\[i2c\]"):
            config.connection.build()


# ============ I2CInterface(config=...) ============


def test_interface_from_config_dict_reads_through_built_definition() -> None:
    """A config-built bus issues the same wire transaction as a hand-built SystemDefinition."""
    driver = _stub_driver()
    i2c = I2CInterface(config=_minimal_config(), driver=driver)
    assert i2c.name == "bus"
    measurement = i2c.read("gpio", "status")
    driver.write_read.assert_called_once_with(0x20, bytes([0x10]), 1)
    assert "bus.gpio.status" in measurement.channel_data


def test_interface_from_config_path_uses_connection_when_no_driver(tmp_path) -> None:
    pytest.importorskip("instro.i2c.drivers.totalphase")
    i2c = I2CInterface(config=CONFIG_PATH)
    assert i2c.name == "sensor_bus"
    assert type(i2c._driver).__name__ == "Aardvark"


def test_interface_explicit_name_overrides_config_device_name() -> None:
    i2c = I2CInterface(name="custom", config=_minimal_config(), driver=_stub_driver())
    assert i2c.name == "custom"


def test_interface_config_object_is_copied_not_aliased() -> None:
    config = I2CConfig.model_validate(_minimal_config())
    i2c = I2CInterface(config=config, driver=_stub_driver())
    assert i2c._config is not config
    assert i2c._config == config


def test_interface_config_without_connection_or_driver_raises() -> None:
    with pytest.raises(ValueError, match="No connection configuration"):
        I2CInterface(config=_minimal_config())


def test_interface_config_and_system_definition_raise() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        I2CInterface(name="x", driver=_stub_driver(), system_definition=SystemDefinition(), config=_minimal_config())


def test_interface_without_config_or_system_definition_raises() -> None:
    with pytest.raises(ValueError, match="requires either config"):
        I2CInterface(name="x", driver=_stub_driver())


def test_interface_system_definition_is_deprecated_but_works() -> None:
    sysdef = SystemDefinition()
    sysdef.add_device(RegisterDevice(name="gpio", address=0x20, registers={"status": RegisterDef("status", 0x10)}))
    with pytest.warns(DeprecationWarning, match="system_definition"):
        i2c = I2CInterface("legacy", _stub_driver(), sysdef)
    assert "legacy.gpio.status" in i2c.read("gpio", "status").channel_data
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="name and driver"):
        I2CInterface(system_definition=sysdef)


# ============ Polling ============


def test_poll_entries_register_daemon_functions_and_timing_sets_interval() -> None:
    i2c = I2CInterface(config=CONFIG_PATH, driver=_stub_driver())
    assert i2c._background_methods == [
        (i2c.read, ("power_gpio", "LED_OUTPUT_STATE"), {}),
        (i2c.query, ("VOLTAGE_ADC", "ch0"), {}),
    ]
    assert i2c.background_interval == 0.5


def test_no_poll_entries_registers_nothing() -> None:
    i2c = I2CInterface(config=_minimal_config(), driver=_stub_driver())
    assert i2c._background_methods == []


def test_autostart_requires_timing() -> None:
    with pytest.raises(ValueError, match="timing"):
        I2CInterface(config=_minimal_config(), driver=_stub_driver(), autostart=True)


def test_autostart_opens_and_starts() -> None:
    driver = _stub_driver()
    i2c = I2CInterface(config=_minimal_config(timing={"poll_interval": 0.05}), driver=driver, autostart=True)
    try:
        driver.open.assert_called_once()
        assert i2c._background_thread is not None and i2c._background_thread.is_alive()
    finally:
        i2c.close()
    driver.close.assert_called_once()


# ============ Runtime scaling ============


def test_set_scaling_on_register_device_replaces_config_scaling() -> None:
    i2c = I2CInterface(config=CONFIG_PATH, driver=_stub_driver())
    i2c.set_scaling("power_gpio", CustomScaling(to_physical_fn=lambda raw: raw * 100.0), register_alias="LED_DIRECTION")
    # The stub answers every read with 0x01 0x20; the 8-bit format keeps the low byte (0x20) before scaling.
    assert i2c.read("power_gpio", "LED_DIRECTION").latest == 0x20 * 100.0


def test_set_scaling_on_command_device() -> None:
    i2c = I2CInterface(config=CONFIG_PATH, driver=_stub_driver())
    i2c.set_scaling("VOLTAGE_ADC", CustomScaling(to_physical_fn=lambda raw: float(raw)))
    # 0x0120 with data_lsb=4, width 12 -> 0x012
    assert i2c.query("VOLTAGE_ADC", "ch1").latest == float(0x012)


def test_set_scaling_register_alias_rules() -> None:
    i2c = I2CInterface(config=CONFIG_PATH, driver=_stub_driver())
    scaling = LinearScaling(gain=2.0)
    with pytest.raises(ValueError, match="register_alias is required"):
        i2c.set_scaling("power_gpio", scaling)
    with pytest.raises(ValueError, match="does not apply"):
        i2c.set_scaling("VOLTAGE_ADC", scaling, register_alias="x")
