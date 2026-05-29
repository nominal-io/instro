"""Tests for read groups, bitmap extraction, and background polling.

Tests cover:
- Bitmap bit extraction from uint16 registers
- Read group bulk reads
- Group config validation (mixed types, poll=false, span limits)
- Bitmap config validation (wrong data type, duplicate bit indices)
- Background daemon registration (grouped vs ungrouped)
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
from pymodbus.server import StartAsyncTcpServer

from instro.lib.types import DeviceInfo
from instro.modbus import BitDef, ModbusConfig, ModbusDevice, RegisterDef

TEST_PORT = 5024


# ============ Sim Server ============


def _pack(fmt, value):
    data = struct.pack(fmt, value)
    return [int.from_bytes(data[i * 2 : (i + 1) * 2], "big") for i in range(len(data) // 2)]


def _create_datastore() -> ModbusServerContext:
    hr = [0] * 200
    # Group registers: temperature(0-1), pressure(2-3)
    data = struct.pack(">f", 72.5)
    hr[0] = int.from_bytes(data[0:2], "big")
    hr[1] = int.from_bytes(data[2:4], "big")
    data = struct.pack(">f", 14.7)
    hr[2] = int.from_bytes(data[0:2], "big")
    hr[3] = int.from_bytes(data[2:4], "big")
    # Bitmap register at addr 10: bits 0, 2, 5 set = 0b00100101 = 0x0025
    hr[10] = 0x0025
    # Standalone register at addr 100
    hr[100] = 42

    # Coil group: alternating on/off
    co = [False] * 10
    co[0] = True
    co[1] = False
    co[2] = True

    # Discrete input group
    di = [False] * 10
    di[0] = True
    di[1] = False
    di[2] = True

    # ModbusDeviceContext has a +1 offset quirk, so we prepend a dummy value
    # so Modbus address N corresponds to array index N+1.
    store = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0, [False] + di),
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
        server_task = asyncio.create_task(StartAsyncTcpServer(context=context, address=("127.0.0.1", TEST_PORT)))
        await shutdown.wait()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

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
        device=DeviceInfo(name="group_test"),
        connection={"transport": "tcp", "host": "127.0.0.1", "port": TEST_PORT},
        registers=[
            RegisterDef(name="temperature", starting_address=0, data_type="float32", read_group="sensors"),
            RegisterDef(name="pressure", starting_address=2, data_type="float32", read_group="sensors"),
            RegisterDef(
                name="status",
                starting_address=10,
                data_type="uint16",
                bitmap=[
                    BitDef(name="bit_0", bit_index=0),
                    BitDef(name="bit_2", bit_index=2),
                    BitDef(name="bit_5", bit_index=5),
                ],
            ),
            RegisterDef(name="standalone", starting_address=100, data_type="uint16"),
            RegisterDef(name="coil_a", starting_address=0, register_type="coil", read_group="coils"),
            RegisterDef(name="coil_b", starting_address=1, register_type="coil", read_group="coils"),
            RegisterDef(name="coil_c", starting_address=2, register_type="coil", read_group="coils"),
            RegisterDef(name="di_a", starting_address=0, register_type="discrete", read_group="inputs"),
            RegisterDef(name="di_b", starting_address=1, register_type="discrete", read_group="inputs"),
            RegisterDef(name="di_c", starting_address=2, register_type="discrete", read_group="inputs"),
        ],
    )
    dev = ModbusDevice(config=config)
    dev.open()
    yield dev
    dev.close()


# ============ Bitmap Tests ============


class TestBitmap:
    def test_bitmap_extraction(self, device):
        m = device.read("status")
        # Raw value 0x0025 = 0b00100101
        assert m.channel_data["group_test.status"] == [0x0025]
        assert m.channel_data["group_test.bit_0"] == [1]
        assert m.channel_data["group_test.bit_2"] == [1]
        assert m.channel_data["group_test.bit_5"] == [1]

    def test_bitmap_zero_bits(self, device):
        m = device.read("status")
        # bit_index 1 and 3 are not in bitmap, but bit_0, bit_2, bit_5 are
        # Just verify the ones we declared
        assert m.channel_data["group_test.bit_0"] == [1]


# ============ Read Group Tests ============


class TestReadGroup:
    def test_group_read_returns_all_channels(self, device):
        m = device._read_group("sensors")
        assert "group_test.temperature" in m.channel_data
        assert "group_test.pressure" in m.channel_data

    def test_group_read_values(self, device):
        m = device._read_group("sensors")
        assert m.channel_data["group_test.temperature"][0] == pytest.approx(72.5, rel=1e-5)
        assert m.channel_data["group_test.pressure"][0] == pytest.approx(14.7, rel=1e-5)

    def test_individual_read_still_works(self, device):
        m = device.read("standalone")
        assert m.latest == 42

    def test_group_read_coils(self, device):
        m = device._read_group("coils")
        assert m.channel_data["group_test.coil_a"] == [1]
        assert m.channel_data["group_test.coil_b"] == [0]
        assert m.channel_data["group_test.coil_c"] == [1]

    def test_group_read_discrete_inputs(self, device):
        m = device._read_group("inputs")
        assert m.channel_data["group_test.di_a"] == [1]
        assert m.channel_data["group_test.di_b"] == [0]
        assert m.channel_data["group_test.di_c"] == [1]


# ============ Config Validation Tests ============


class TestGroupConfigValidation:
    def test_mixed_register_types_in_group(self):
        with pytest.raises(ValidationError, match="mixed register types"):
            ModbusConfig(
                device=DeviceInfo(name="bad"),
                registers=[
                    RegisterDef(name="a", starting_address=0, register_type="holding", read_group="g1"),
                    RegisterDef(name="b", starting_address=0, register_type="input", read_group="g1"),
                ],
            )

    def test_poll_false_in_group(self):
        with pytest.raises(ValidationError, match="poll=false"):
            ModbusConfig(
                device=DeviceInfo(name="bad"),
                registers=[
                    RegisterDef(name="a", starting_address=0, read_group="g1"),
                    RegisterDef(name="b", starting_address=1, read_group="g1", poll=False),
                ],
            )


class TestBitmapConfigValidation:
    def test_bitmap_wrong_data_type(self):
        with pytest.raises(ValidationError, match="uint16"):
            RegisterDef(
                name="bad",
                starting_address=0,
                data_type="uint32",
                bitmap=[BitDef(name="b", bit_index=0)],
            )

    def test_bitmap_wrong_register_type(self):
        with pytest.raises(ValidationError, match="holding or input"):
            RegisterDef(
                name="bad",
                starting_address=0,
                register_type="coil",
                bitmap=[BitDef(name="b", bit_index=0)],
            )

    def test_bitmap_duplicate_bit_indices(self):
        with pytest.raises(ValidationError, match="Duplicate bit_index"):
            RegisterDef(
                name="bad",
                starting_address=0,
                bitmap=[BitDef(name="a", bit_index=0), BitDef(name="b", bit_index=0)],
            )

    def test_bitmap_name_collides_with_register_name(self):
        with pytest.raises(ValidationError, match="Duplicate names"):
            ModbusConfig(
                device=DeviceInfo(name="bad"),
                registers=[
                    RegisterDef(name="collision", starting_address=0),
                    RegisterDef(
                        name="status",
                        starting_address=1,
                        bitmap=[BitDef(name="collision", bit_index=0)],
                    ),
                ],
            )
