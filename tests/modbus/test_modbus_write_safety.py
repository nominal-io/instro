"""Tests for write safety features: value maps, min/max limits, type checking.

Tests cover:
- write_value_map resolution (string -> numeric)
- write_min / write_max enforcement
- Type checking (bool to int register, float to int register, etc.)
- Config validation of write fields (on read-only registers, inverted min/max, etc.)
"""

import asyncio
import struct
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import ModbusTcpServer

from instro.lib.types import DeviceInfo, LinearScale
from instro.modbus import ModbusConfig, ModbusDevice, RegisterDef

TEST_PORT = 5023


# ============ Sim Server ============


def _create_datastore() -> ModbusServerContext:
    hr = [0] * 200
    hr[0] = 100  # mode register
    hr[10] = 500  # limited register

    co = [False] * 10

    store = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0, [False] * 10),
        co=ModbusSequentialDataBlock(0, [False] + co),
        hr=ModbusSequentialDataBlock(0, [0] + hr),
        ir=ModbusSequentialDataBlock(0, [0] * 10),
    )
    return ModbusServerContext(devices={1: store}, single=False)


@pytest.fixture(scope="module")
def modbus_server():
    loop = asyncio.new_event_loop()
    context = _create_datastore()
    shutdown: asyncio.Event | None = None

    async def _run():
        nonlocal shutdown
        shutdown = asyncio.Event()
        server = ModbusTcpServer(context=context, address=("127.0.0.1", TEST_PORT))
        await server.serve_forever(background=True)
        try:
            await shutdown.wait()
        finally:
            await server.shutdown()

    def _thread_target():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    thread = threading.Thread(target=_thread_target, daemon=True)
    thread.start()
    time.sleep(0.3)
    yield
    assert shutdown is not None
    loop.call_soon_threadsafe(shutdown.set)
    thread.join(timeout=2.0)


@pytest.fixture
def device(modbus_server):
    config = ModbusConfig(
        device=DeviceInfo(name="write_test"),
        connection={"transport": "tcp", "host": "127.0.0.1", "port": TEST_PORT},
        registers=[
            RegisterDef(
                name="mode",
                starting_address=0,
                data_type="uint16",
                write_value_map={"off": 0, "heat": 1, "cool": 2, "auto": 3},
            ),
            RegisterDef(
                name="limited",
                starting_address=10,
                data_type="uint16",
                write_min=100,
                write_max=1000,
            ),
            RegisterDef(name="plain_uint16", starting_address=20, data_type="uint16"),
            RegisterDef(name="plain_float32", starting_address=30, data_type="float32"),
            RegisterDef(name="coil", starting_address=0, register_type="coil", data_type="bool"),
            RegisterDef(name="input_reg", starting_address=0, register_type="input", data_type="uint16"),
        ],
    )
    dev = ModbusDevice(config=config)
    dev.open()
    yield dev
    dev.close()


# ============ Value Map Tests ============


class TestValueMap:
    def test_write_string_key(self, device):
        device.write("mode", "heat")
        m = device.read("mode")
        assert m.latest == 1

    def test_write_string_key_roundtrip(self, device):
        for label, expected in [("off", 0), ("heat", 1), ("cool", 2), ("auto", 3)]:
            device.write("mode", label)
            m = device.read("mode")
            assert m.latest == expected

    def test_write_numeric_still_works(self, device):
        device.write("mode", 2)
        m = device.read("mode")
        assert m.latest == 2

    def test_write_invalid_string_key(self, device):
        with pytest.raises(KeyError, match="not a valid value"):
            device.write("mode", "turbo")

    def test_write_string_without_value_map(self, device):
        with pytest.raises(KeyError, match="no write_value_map"):
            device.write("plain_uint16", "some_string")


# ============ Write Limits Tests ============


class TestWriteLimits:
    def test_write_within_limits(self, device):
        device.write("limited", 500)
        m = device.read("limited")
        assert m.latest == 500

    def test_write_below_min(self, device):
        with pytest.raises(ValueError, match="below write_min"):
            device.write("limited", 50)

    def test_write_above_max(self, device):
        with pytest.raises(ValueError, match="above write_max"):
            device.write("limited", 1500)

    def test_write_at_boundary(self, device):
        device.write("limited", 100)
        assert device.read("limited").latest == 100
        device.write("limited", 1000)
        assert device.read("limited").latest == 1000


# ============ Type Checking Tests ============


class TestTypeChecking:
    def test_bool_to_int_register_rejected(self, device):
        with pytest.raises(TypeError, match="integer type.*got bool"):
            device.write("plain_uint16", True)

    def test_fractional_float_to_int_register_rejected(self, device):
        with pytest.raises(TypeError, match="float"):
            device.write("plain_uint16", 1.5)

    def test_bool_to_float_register_rejected(self, device):
        with pytest.raises(TypeError, match="float type.*got bool"):
            device.write("plain_float32", True)

    def test_write_to_input_register_rejected(self, device):
        with pytest.raises(ValueError, match="read-only"):
            device.write("input_reg", 0)

    def test_coil_accepts_bool(self, device):
        device.write("coil", True)
        assert device.read("coil").latest == 1

    def test_coil_accepts_zero_one(self, device):
        device.write("coil", 0)
        assert device.read("coil").latest == 0


# ============ Config Validation Tests ============


class TestWriteFieldConfigValidation:
    def test_write_fields_on_input_rejected(self):
        with pytest.raises(ValidationError, match="holding registers"):
            RegisterDef(name="bad", starting_address=0, register_type="input", write_min=0)

    def test_inverted_min_max_rejected(self):
        with pytest.raises(ValidationError, match="less than or equal"):
            RegisterDef(name="bad", starting_address=0, write_min=100, write_max=10)

    def test_value_map_duplicate_values_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate value"):
            RegisterDef(
                name="bad",
                starting_address=0,
                write_value_map={"a": 1, "b": 1},
            )

    def test_value_map_exceeds_write_max_rejected(self):
        with pytest.raises(ValidationError, match="above write_max"):
            RegisterDef(
                name="bad",
                starting_address=0,
                write_max=10,
                write_value_map={"ok": 5, "bad": 20},
            )

    def test_value_map_float_for_int_type_rejected(self):
        with pytest.raises(ValidationError, match="non-integer float"):
            RegisterDef(
                name="bad",
                starting_address=0,
                data_type="uint16",
                write_value_map={"x": 1.5},
            )

    def test_value_map_bool_for_int_register_rejected(self):
        # Pydantic would silently coerce True -> 1 under dict[str, int | float], hiding
        # the author's intent. A mode="before" model validator catches bool entries on
        # non-bool registers and rejects them, mirroring the runtime rejection of direct
        # bool writes to integer registers.
        with pytest.raises(ValidationError, match="is a bool"):
            RegisterDef(
                name="bad",
                starting_address=0,
                data_type="uint16",
                write_value_map={"enable": True},
            )

    def test_value_map_bool_allowed_on_bool_register(self):
        # On a bool-type register, bool is the natural value type — keep it accepted.
        reg = RegisterDef(
            name="ok",
            starting_address=0,
            data_type="bool",
            write_value_map={"on": True, "off": False},
        )
        # Pydantic still coerces stored values to int under the dict[str, int | float]
        # annotation; the point here is just that construction didn't raise.
        assert set(reg.write_value_map.keys()) == {"on", "off"}

    def test_value_map_fractional_float_allowed_when_scale_converts_to_integer(self):
        # gain=0.1 means physical 22.5 -> raw 225, which is a valid uint16.
        # The old validator unconditionally rejected fractional floats; the runtime
        # only rejects them when scale is None, so this config should be accepted.
        reg = RegisterDef(
            name="ok",
            starting_address=0,
            data_type="uint16",
            scale=LinearScale(gain=0.1),
            write_value_map={"comfort": 22.5},
        )
        assert reg.write_value_map == {"comfort": 22.5}

    def test_value_map_rejects_scaled_raw_that_is_not_an_integer(self):
        # gain=3 means physical 5 -> raw 1.666..., which cannot land on an
        # integer register. Runtime _validate_raw_value_range rejects this
        # (abs(raw - round(raw)) > 1e-6); config validation must match.
        with pytest.raises(ValidationError, match="not an integer"):
            RegisterDef(
                name="bad",
                starting_address=0,
                data_type="uint16",
                scale=LinearScale(gain=3),
                write_value_map={"mode_a": 5},
            )

    def test_value_map_on_bool_register_rejects_float(self):
        # bool-type registers (and coils at runtime) accept only bool or int in {0, 1};
        # runtime _validate_write_value rejects any other type. Config must match.
        # Using a holding register with data_type="bool" exercises this without
        # coupling the test to whether write_value_map is allowed on coil itself.
        with pytest.raises(ValidationError, match="True/False or 0/1"):
            RegisterDef(
                name="bad",
                starting_address=0,
                data_type="bool",
                write_value_map={"on": 1.0},
            )

    def test_value_map_on_bool_register_rejects_non_binary_int(self):
        with pytest.raises(ValidationError, match="True/False or 0/1"):
            RegisterDef(
                name="bad",
                starting_address=0,
                data_type="bool",
                write_value_map={"turbo": 5},
            )

    def test_value_map_on_bool_register_accepts_bool_and_binary_int(self):
        reg = RegisterDef(
            name="ok",
            starting_address=0,
            data_type="bool",
            write_value_map={"on": True, "off": 0},
        )
        assert reg.write_value_map == {"on": True, "off": 0}
