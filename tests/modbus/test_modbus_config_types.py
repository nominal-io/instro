"""Unit tests for Modbus config types (ModbusConfig, RegisterDef, connections).

Tests cover custom logic only — Pydantic field defaults and constraints are not tested.
Scaling tests are in test_protocol_scaling.py.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from instro.lib.types import DeviceInfo, LinearScale
from instro.modbus import ModbusConfig, RegisterDef, RTUConnection, TCPConnection

CONFIGS_DIR = Path(__file__).parent / "configs"
CONFIG_PATH = CONFIGS_DIR / "test_config_types.json"


# ============ JSON Loading ============


class TestFromJson:
    def test_load_from_json(self):
        config = ModbusConfig.from_json(CONFIG_PATH)
        assert config.device.name == "test_device"
        assert len(config.registers) == 8

    def test_load_from_string_path(self):
        config = ModbusConfig.from_json(str(CONFIG_PATH))
        assert config.device.name == "test_device"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            ModbusConfig.from_json("/nonexistent/path.json")

    def test_malformed_json(self):
        with pytest.raises(Exception):
            ModbusConfig.from_json(CONFIGS_DIR / "malformed.json")

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            ModbusConfig.from_json(CONFIGS_DIR / "missing_device.json")


# ============ Connection Discriminator ============


class TestConnectionDiscriminator:
    def test_tcp_from_json(self):
        config = ModbusConfig.from_json(CONFIG_PATH)
        conn = config.connection
        assert isinstance(conn, TCPConnection)
        assert conn.host == "127.0.0.1"
        assert conn.port == 5021

    def test_rtu_from_config(self):
        config = ModbusConfig(
            device=DeviceInfo(name="rtu_device"),
            connection=RTUConnection(port="/dev/cu.usbserial-1234", baudrate=19200),
        )
        assert isinstance(config.connection, RTUConnection)
        assert config.connection.port == "/dev/cu.usbserial-1234"

    def test_rtu_discriminator_from_dict(self):
        config = ModbusConfig.model_validate(
            {
                "device": {"name": "rtu_test"},
                "connection": {"transport": "rtu", "port": "/dev/ttyUSB0"},
            }
        )
        assert isinstance(config.connection, RTUConnection)

    def test_no_connection_is_valid(self):
        config = ModbusConfig(device=DeviceInfo(name="no_conn"))
        assert config.connection is None


# ============ Timing ============


class TestTimingConfig:
    def test_timing_from_json(self):
        config = ModbusConfig.from_json(CONFIG_PATH)
        assert config.timing is not None
        assert config.timing.poll_interval == 0.5
        assert config.timing.write_delay_ms == 50

    def test_timing_optional(self):
        config = ModbusConfig(device=DeviceInfo(name="no_timing"))
        assert config.timing is None


# ============ Register Definition ============


class TestRegisterDef:
    def test_register_count_16bit(self):
        for dt in ("uint16", "int16", "bool"):
            reg = RegisterDef(name="test", starting_address=0, data_type=dt)
            assert reg.register_count == 1, f"{dt} should span 1 register"

    def test_register_count_32bit(self):
        for dt in ("uint32", "int32", "float32"):
            reg = RegisterDef(name="test", starting_address=0, data_type=dt)
            assert reg.register_count == 2, f"{dt} should span 2 registers"

    def test_register_count_64bit(self):
        for dt in ("uint64", "int64", "float64"):
            reg = RegisterDef(name="test", starting_address=0, data_type=dt)
            assert reg.register_count == 4, f"{dt} should span 4 registers"

    def test_description_from_json(self):
        config = ModbusConfig.from_json(CONFIG_PATH)
        reg = config.get_register("setpoint")
        assert reg.description == "Temperature setpoint in degrees C"

    def test_swap_flags_from_json(self):
        config = ModbusConfig.from_json(CONFIG_PATH)
        reg = config.get_register("swapped_value")
        assert reg.word_swap is True
        assert reg.byte_swap is False
        assert reg.long_swap is False


# ============ Scaling Integration ============


class TestScalingFromConfig:
    def test_scale_from_config(self):
        config = ModbusConfig.from_json(CONFIG_PATH)
        reg = config.get_register("setpoint")
        assert reg.scale is not None
        assert reg.scale.to_physical(250) == pytest.approx(25.0)


# ============ ModbusConfig Validation ============


class TestModbusConfigValidation:
    def test_wrong_protocol(self):
        with pytest.raises(ValidationError, match="expected 'modbus'"):
            ModbusConfig(
                protocol="scpi",
                device=DeviceInfo(name="wrong"),
            )

    def test_duplicate_register_names(self):
        with pytest.raises(ValidationError, match="Duplicate register names"):
            ModbusConfig(
                device=DeviceInfo(name="dupes"),
                registers=[
                    RegisterDef(name="temp", starting_address=0),
                    RegisterDef(name="temp", starting_address=1),
                ],
            )


# ============ ModbusConfig.get_register ============


class TestGetRegister:
    def test_get_existing(self):
        config = ModbusConfig.from_json(CONFIG_PATH)
        reg = config.get_register("temperature")
        assert reg.name == "temperature"
        assert reg.starting_address == 0
        assert reg.data_type == "float32"

    def test_get_missing(self):
        config = ModbusConfig.from_json(CONFIG_PATH)
        with pytest.raises(KeyError, match="not found"):
            config.get_register("nonexistent")


# ============ Programmatic Construction ============


class TestProgrammaticConfig:
    def test_minimal_config(self):
        config = ModbusConfig(
            device=DeviceInfo(name="prog_test"),
            registers=[
                RegisterDef(name="reg1", starting_address=0, data_type="uint16"),
            ],
        )
        assert config.device.name == "prog_test"
        assert config.get_register("reg1").starting_address == 0

    def test_dict_construction(self):
        config = ModbusConfig.model_validate(
            {
                "device": {"name": "dict_test"},
                "connection": {"transport": "tcp", "host": "10.0.0.1", "port": 502},
                "registers": [
                    {"name": "r1", "starting_address": 0, "data_type": "float32"},
                ],
            }
        )
        assert config.device.name == "dict_test"
        assert isinstance(config.connection, TCPConnection)
        assert config.get_register("r1").data_type == "float32"
