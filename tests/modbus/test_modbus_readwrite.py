"""Integration tests for ModbusDevice read/write against a simulated device.

Tests cover:
- Reading each data type (uint16, int16, uint32, int32, float32, float64)
- Reading coils and discrete inputs
- Writing to holding registers and coils with read-back verification
- Scaling (read path)
- Word swap encoding/decoding
- Write to read-only register raises error
- Connection lifecycle (open/close)
"""

import asyncio
import struct
import threading
import time
from pathlib import Path

import pytest
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer

from instro.modbus import ModbusDevice

CONFIGS_DIR = Path(__file__).parent / "configs"
CONFIG_PATH = CONFIGS_DIR / "test_readwrite_device.json"
TEST_PORT = 5022

# ============ Test Data ============

TEST_DATA = {
    "input_uint16": 12345,
    "input_int16": -4567,
    "input_uint32": 123456789,
    "input_int32": -123456789,
    "input_float32": 123.456,
    "input_float64": 12345.6789012345,
    "input_scaled": 1000,  # raw; scaled = 5.0 + 0.1 * 1000 = 105.0
    "holding_uint16": 54321,
    "holding_int32": -987654321,
    "holding_float32": 654.321,
    "holding_word_swap": 0xDEADBEEF,
    "coil_1": False,
    "coil_2": True,
    "discrete_1": True,
    "discrete_2": False,
}


# ============ Sim Server Fixture ============


def _pack(fmt: str, value) -> list[int]:
    data = struct.pack(fmt, value)
    return [int.from_bytes(data[i * 2 : (i + 1) * 2], "big") for i in range(len(data) // 2)]


def _create_datastore() -> ModbusServerContext:
    ir = [0] * 200
    ir[0] = TEST_DATA["input_uint16"]
    ir[1] = struct.unpack(">H", struct.pack(">h", TEST_DATA["input_int16"]))[0]
    ir[10:12] = _pack(">I", TEST_DATA["input_uint32"])
    ir[12:14] = _pack(">i", TEST_DATA["input_int32"])
    ir[30:32] = _pack(">f", TEST_DATA["input_float32"])
    ir[40:44] = _pack(">d", TEST_DATA["input_float64"])
    ir[50] = TEST_DATA["input_scaled"]

    hr = [0] * 200
    hr[100] = TEST_DATA["holding_uint16"]
    hr[110:112] = _pack(">i", TEST_DATA["holding_int32"])
    hr[120:122] = _pack(">f", TEST_DATA["holding_float32"])
    # word_swap: swap 16-bit words before storing so the driver's word_swap decode reads correctly
    ws_regs = _pack(">I", TEST_DATA["holding_word_swap"])
    hr[130], hr[131] = ws_regs[1], ws_regs[0]  # swapped

    co = [False] * 10
    co[0] = TEST_DATA["coil_1"]
    co[1] = TEST_DATA["coil_2"]

    di = [False] * 10
    di[0] = TEST_DATA["discrete_1"]
    di[1] = TEST_DATA["discrete_2"]

    # ModbusDeviceContext has a +1 offset quirk, so we prepend a dummy value
    # so Modbus address N corresponds to array index N+1.
    store = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0, [False] + di),
        co=ModbusSequentialDataBlock(0, [False] + co),
        hr=ModbusSequentialDataBlock(0, [0] + hr),
        ir=ModbusSequentialDataBlock(0, [0] + ir),
    )
    return ModbusServerContext(devices={1: store}, single=False)


@pytest.fixture(scope="module")
def modbus_server():
    """Start a sim Modbus TCP server in a background thread for the test module."""
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
    time.sleep(0.3)  # wait for server to bind
    yield
    assert shutdown is not None
    loop.call_soon_threadsafe(shutdown.set)
    thread.join(timeout=2.0)


@pytest.fixture
def device(modbus_server):
    """Create a connected ModbusDevice instance."""
    dev = ModbusDevice(config=CONFIG_PATH)
    dev.open()
    yield dev
    dev.close()


# ============ Read Tests ============


class TestReadInputRegisters:
    def test_read_uint16(self, device):
        m = device.read("input_uint16")
        assert m.latest == TEST_DATA["input_uint16"]

    def test_read_int16(self, device):
        m = device.read("input_int16")
        assert m.latest == TEST_DATA["input_int16"]

    def test_read_uint32(self, device):
        m = device.read("input_uint32")
        assert m.latest == TEST_DATA["input_uint32"]

    def test_read_int32(self, device):
        m = device.read("input_int32")
        assert m.latest == TEST_DATA["input_int32"]

    def test_read_float32(self, device):
        m = device.read("input_float32")
        assert m.latest == pytest.approx(TEST_DATA["input_float32"], rel=1e-5)

    def test_read_float64(self, device):
        m = device.read("input_float64")
        assert m.latest == pytest.approx(TEST_DATA["input_float64"], rel=1e-10)

    def test_read_scaled(self, device):
        m = device.read("input_scaled")
        expected = 5.0 + 0.1 * TEST_DATA["input_scaled"]  # 105.0
        assert m.latest == pytest.approx(expected)


class TestReadBoolRegisters:
    def test_read_coil(self, device):
        m = device.read("coil_2")
        assert m.latest == 1  # True -> 1

    def test_read_discrete(self, device):
        m = device.read("discrete_1")
        assert m.latest == 1

    def test_read_discrete_false(self, device):
        m = device.read("discrete_2")
        assert m.latest == 0


class TestReadHoldingRegisters:
    def test_read_uint16(self, device):
        m = device.read("holding_uint16")
        assert m.latest == TEST_DATA["holding_uint16"]

    def test_read_int32(self, device):
        m = device.read("holding_int32")
        assert m.latest == TEST_DATA["holding_int32"]

    def test_read_float32(self, device):
        m = device.read("holding_float32")
        assert m.latest == pytest.approx(TEST_DATA["holding_float32"], rel=1e-5)

    def test_read_word_swap(self, device):
        m = device.read("holding_word_swap")
        assert m.latest == TEST_DATA["holding_word_swap"]


# ============ Write Tests ============


class TestWriteHoldingRegisters:
    def test_write_uint16_roundtrip(self, device):
        device.write("holding_uint16", 11111)
        m = device.read("holding_uint16")
        assert m.latest == 11111

    def test_write_int32_roundtrip(self, device):
        device.write("holding_int32", -42)
        m = device.read("holding_int32")
        assert m.latest == -42

    def test_write_float32_roundtrip(self, device):
        device.write("holding_float32", 99.5)
        m = device.read("holding_float32")
        assert m.latest == pytest.approx(99.5, rel=1e-5)

    def test_write_returns_command(self, device):
        cmd = device.write("holding_uint16", 1234)
        assert f"test_rw_device.holding_uint16.cmd" in cmd.channel_data

    def test_write_preserves_integer_value_type(self, device):
        """Modbus integer writes publish the int value untouched (not coerced to float)."""
        cmd = device.write("holding_uint16", 1234)
        value = cmd.channel_data["test_rw_device.holding_uint16.cmd"]
        assert value == 1234
        assert isinstance(value, int)
        assert not isinstance(value, bool)

    def test_write_preserves_bool_value_type_for_coil(self, device):
        """Modbus coil writes publish True/False as bool, not coerced to 1.0/0.0."""
        cmd = device.write("coil_1", True)
        value = cmd.channel_data["test_rw_device.coil_1.cmd"]
        assert value is True

    def test_write_scaled_integer_rounding(self, device):
        # 0.9 / 0.3 = 2.9999999999999996 in floating point; round() must be used,
        # not int(), to avoid truncation to 2 instead of 3.
        device.write("holding_scaled_uint16", 0.9)
        m = device.read("holding_scaled_uint16")
        assert m.latest == pytest.approx(0.9, rel=1e-5)


class TestWriteCoils:
    def test_write_coil_true(self, device):
        device.write("coil_1", True)
        m = device.read("coil_1")
        assert m.latest == 1

    def test_write_coil_false(self, device):
        device.write("coil_1", False)
        m = device.read("coil_1")
        assert m.latest == 0


class TestWriteErrors:
    def test_write_to_input_register_raises(self, device):
        with pytest.raises(ValueError, match="read-only"):
            device.write("input_uint16", 0)

    def test_write_to_discrete_input_raises(self, device):
        with pytest.raises(ValueError, match="read-only"):
            device.write("discrete_1", True)


# ============ Connection Lifecycle ============


class TestConnection:
    def test_read_before_open_raises(self):
        dev = ModbusDevice(config=CONFIG_PATH)
        with pytest.raises(RuntimeError, match="not connected"):
            dev.read("input_uint16")

    def test_close_and_reopen(self, modbus_server):
        dev = ModbusDevice(config=CONFIG_PATH)
        dev.open()
        dev.read("input_uint16")
        dev.close()
        dev.open()
        m = dev.read("input_uint16")
        assert m.latest == TEST_DATA["input_uint16"]
        dev.close()

    def test_measurement_channel_naming(self, device):
        m = device.read("input_uint16")
        assert "test_rw_device.input_uint16" in m.channel_data
