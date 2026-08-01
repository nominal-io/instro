"""Tests for the standalone ModbusDriver transport against a simulated Modbus TCP server."""

from __future__ import annotations

import asyncio
import logging
import struct
import threading
import time
from unittest.mock import Mock, patch

import pytest
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.exceptions import ConnectionException
from pymodbus.server import StartAsyncTcpServer

from instro.lib.transports import ModbusDriver, RTUConnection, TCPConnection

TEST_PORT = 5031

TEST_DATA = {
    "input_uint16": 12345,
    "input_int16": -4567,
    "input_uint32": 123456789,
    "input_float32": 123.456,
    "input_float64": 12345.6789012345,
    "holding_uint16": 54321,
    "holding_word_swap": 0xDEADBEEF,
    "coil_1": False,
    "coil_2": True,
    "discrete_1": True,
    "discrete_2": False,
}


def _pack(fmt: str, value) -> list[int]:
    data = struct.pack(fmt, value)
    return [int.from_bytes(data[i * 2 : (i + 1) * 2], "big") for i in range(len(data) // 2)]


def _create_datastore() -> ModbusServerContext:
    ir = [0] * 200
    ir[0] = TEST_DATA["input_uint16"]
    ir[1] = struct.unpack(">H", struct.pack(">h", TEST_DATA["input_int16"]))[0]
    ir[10:12] = _pack(">I", TEST_DATA["input_uint32"])
    ir[30:32] = _pack(">f", TEST_DATA["input_float32"])
    ir[40:44] = _pack(">d", TEST_DATA["input_float64"])

    hr = [0] * 200
    hr[100] = TEST_DATA["holding_uint16"]
    ws_regs = _pack(">I", TEST_DATA["holding_word_swap"])
    hr[130], hr[131] = ws_regs[1], ws_regs[0]  # stored word-swapped

    co = [False] * 10
    co[0] = TEST_DATA["coil_1"]
    co[1] = TEST_DATA["coil_2"]

    di = [False] * 10
    di[0] = TEST_DATA["discrete_1"]
    di[1] = TEST_DATA["discrete_2"]

    # ModbusDeviceContext has a +1 offset quirk, so prepend a dummy so Modbus
    # address N maps to array index N+1.
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
def driver(modbus_server):
    """Create a connected ModbusDriver instance."""
    drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
    drv.open()
    yield drv
    drv.close()


# ============ Lifecycle ============


class TestLifecycle:
    def test_unit_id_from_connection(self):
        assert ModbusDriver(TCPConnection(host="h", port=1, unit_id=7)).unit_id == 7

    def test_not_open_before_open(self):
        assert not ModbusDriver(TCPConnection(host="h", port=1)).is_open

    def test_open_close_toggles_is_open(self, modbus_server):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
        assert not drv.is_open
        drv.open()
        assert drv.is_open
        drv.close()
        assert not drv.is_open

    def test_open_is_idempotent(self, modbus_server):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
        drv.open()
        drv.open()  # no raise, no reconnect churn
        assert drv.is_open
        drv.close()

    def test_close_is_idempotent(self, modbus_server):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
        drv.open()
        drv.close()
        drv.close()  # no raise
        assert not drv.is_open

    def test_connect_failure_raises(self):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=1, timeout=0.2))
        with pytest.raises(ConnectionError, match="Failed to connect"):
            drv.open()
        assert not drv.is_open

    def test_op_before_open_raises(self):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
        with pytest.raises(RuntimeError, match="not connected"):
            drv.read_holding_registers(100, 1)

    def test_del_closes_open_driver(self, modbus_server):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
        drv.open()
        drv.__del__()  # best-effort close on GC
        assert not drv.is_open

    def test_del_on_unopened_is_safe(self):
        ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT)).__del__()  # no raise

    def test_reconnects_after_transport_error(self, modbus_server):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
        drv.open()
        try:
            assert drv.read_holding_registers(100, 1) == [TEST_DATA["holding_uint16"]]

            # Force one transport error at the pymodbus client layer; _modbus_op should
            # drop the dead socket and re-raise.
            with patch.object(drv._client, "read_holding_registers", side_effect=ConnectionException("boom")):
                with pytest.raises(ConnectionException):
                    drv.read_holding_registers(100, 1)

            # The next real op reconnects (pymodbus connect() rebuilds the socket) and succeeds.
            assert drv.read_holding_registers(100, 1) == [TEST_DATA["holding_uint16"]]
        finally:
            drv.close()


# ============ Raw Function-Code Ops ============


class TestRawOps:
    def test_read_holding_registers(self, driver):
        assert driver.read_holding_registers(100, 1) == [TEST_DATA["holding_uint16"]]

    def test_read_input_registers(self, driver):
        assert driver.read_input_registers(0, 1) == [TEST_DATA["input_uint16"]]

    def test_read_coils(self, driver):
        assert driver.read_coils(0, 2) == [TEST_DATA["coil_1"], TEST_DATA["coil_2"]]

    def test_read_discrete_inputs(self, driver):
        assert driver.read_discrete_inputs(0, 2) == [TEST_DATA["discrete_1"], TEST_DATA["discrete_2"]]

    def test_write_holding_register_readback(self, driver):
        driver.write_holding_register(150, 4242)
        assert driver.read_holding_registers(150, 1) == [4242]

    def test_write_holding_registers_readback(self, driver):
        driver.write_holding_registers(160, [11, 22, 33])
        assert driver.read_holding_registers(160, 3) == [11, 22, 33]

    def test_write_coil_readback(self, driver):
        driver.write_coil(5, True)
        assert driver.read_coils(5, 1) == [True]

    def test_write_coils_readback(self, driver):
        driver.write_coils(6, [True, False, True])
        assert driver.read_coils(6, 3) == [True, False, True]

    def test_read_beyond_datastore_raises_modbus_error(self, driver):
        # Valid Modbus address, but past the sim datastore -> device returns IllegalDataAddress.
        with pytest.raises(RuntimeError, match="Modbus error"):
            driver.read_holding_registers(500, 1)


# ============ Typed Access ============


class TestTypedAccess:
    def test_read_typed_input_uint16(self, driver):
        assert driver.read_typed("input", 0, "uint16") == TEST_DATA["input_uint16"]

    def test_read_typed_input_int16(self, driver):
        assert driver.read_typed("input", 1, "int16") == TEST_DATA["input_int16"]

    def test_read_typed_input_uint32(self, driver):
        assert driver.read_typed("input", 10, "uint32") == TEST_DATA["input_uint32"]

    def test_read_typed_input_float32(self, driver):
        assert driver.read_typed("input", 30, "float32") == pytest.approx(TEST_DATA["input_float32"], rel=1e-5)

    def test_read_typed_input_float64(self, driver):
        assert driver.read_typed("input", 40, "float64") == pytest.approx(TEST_DATA["input_float64"], rel=1e-10)

    def test_read_typed_holding_word_swap(self, driver):
        assert driver.read_typed("holding", 130, "uint32", word_swap=True) == TEST_DATA["holding_word_swap"]

    def test_read_typed_coil_returns_bool(self, driver):
        result = driver.read_typed("coil", 1, "bool")
        assert result is True

    def test_read_typed_discrete_returns_bool(self, driver):
        result = driver.read_typed("discrete", 1, "bool")
        assert result is False

    def test_read_typed_coil_rejects_non_bool_dtype(self, driver):
        with pytest.raises(ValueError, match="single-bit; data_type must be 'bool'"):
            driver.read_typed("coil", 1, "uint16")

    def test_read_typed_discrete_rejects_non_bool_dtype(self, driver):
        with pytest.raises(ValueError, match="single-bit; data_type must be 'bool'"):
            driver.read_typed("discrete", 1, "uint16")

    def test_read_typed_unknown_register_type_raises(self, driver):
        with pytest.raises(ValueError, match="Unknown register type"):
            driver.read_typed("bogus", 0, "uint16")

    def test_write_typed_holding_single_register_readback(self, driver):
        driver.write_typed("holding", 170, 4321, "uint16")
        assert driver.read_typed("holding", 170, "uint16") == 4321

    def test_write_typed_holding_multi_register_readback(self, driver):
        driver.write_typed("holding", 172, -123456789, "int32")
        assert driver.read_typed("holding", 172, "int32") == -123456789

    def test_write_typed_holding_float_roundtrip(self, driver):
        driver.write_typed("holding", 176, 3.14159, "float32")
        assert driver.read_typed("holding", 176, "float32") == pytest.approx(3.14159, rel=1e-5)

    def test_write_typed_coil_readback(self, driver):
        driver.write_typed("coil", 8, True, "bool")
        assert driver.read_typed("coil", 8, "bool") is True

    def test_write_typed_coil_rejects_non_bool_dtype(self, driver):
        with pytest.raises(ValueError, match="single-bit; data_type must be 'bool'"):
            driver.write_typed("coil", 8, True, "uint16")

    def test_write_typed_coil_rejects_non_bool_value(self, driver):
        # Strict: no numeric coercion. Even 1/0 must be passed as real booleans.
        with pytest.raises(ValueError, match="coil writes require a bool value"):
            driver.write_typed("coil", 8, 1, "bool")

    def test_write_typed_read_only_raises(self, driver):
        with pytest.raises(ValueError, match="read-only"):
            driver.write_typed("input", 0, 1, "uint16")


# ============ Pure Codec ============


class TestCodec:
    def test_data_type_literal_matches_category_constants(self):
        # The transport's DataType Literal and instro.modbus.types.ALL_DATA_TYPES
        # (derived from INTEGER_RANGES) can't share a definition; pin them together.
        from typing import get_args

        from instro.lib.transports.modbus import DataType
        from instro.modbus.types import ALL_DATA_TYPES

        assert set(get_args(DataType)) == set(ALL_DATA_TYPES)

    @pytest.mark.parametrize(
        "data_type,expected",
        [("uint16", 1), ("int16", 1), ("bool", 1), ("uint32", 2), ("float32", 2), ("uint64", 4), ("float64", 4)],
    )
    def test_register_count(self, data_type, expected):
        assert ModbusDriver.register_count(data_type) == expected

    @pytest.mark.parametrize(
        "data_type,value",
        [
            ("uint16", 54321),
            ("int16", -4567),
            ("uint32", 123456789),
            ("int32", -123456789),
            ("uint64", 12345678901234),
            ("int64", -12345678901234),
        ],
    )
    def test_int_roundtrip(self, data_type, value):
        encoded = ModbusDriver.encode_value(value, data_type)
        assert ModbusDriver.decode_registers(encoded, data_type) == value

    @pytest.mark.parametrize("data_type,rel", [("float32", 1e-6), ("float64", 1e-12)])
    def test_float_roundtrip(self, data_type, rel):
        encoded = ModbusDriver.encode_value(3.14159265, data_type)
        assert ModbusDriver.decode_registers(encoded, data_type) == pytest.approx(3.14159265, rel=rel)

    @pytest.mark.parametrize("word_swap", [False, True])
    @pytest.mark.parametrize("byte_swap", [False, True])
    def test_swap_roundtrip(self, word_swap, byte_swap):
        encoded = ModbusDriver.encode_value(0xDEADBEEF, "uint32", byte_swap=byte_swap, word_swap=word_swap)
        decoded = ModbusDriver.decode_registers(encoded, "uint32", byte_swap=byte_swap, word_swap=word_swap)
        assert decoded == 0xDEADBEEF

    @pytest.mark.parametrize("long_swap", [False, True])
    @pytest.mark.parametrize("word_swap", [False, True])
    @pytest.mark.parametrize("byte_swap", [False, True])
    def test_swap_roundtrip_64bit(self, long_swap, word_swap, byte_swap):
        # long_swap only applies to 64-bit types; exercise it across every swap combination.
        value = 0x1337BEEFCAFEBABE
        encoded = ModbusDriver.encode_value(
            value, "uint64", byte_swap=byte_swap, word_swap=word_swap, long_swap=long_swap
        )
        decoded = ModbusDriver.decode_registers(
            encoded, "uint64", byte_swap=byte_swap, word_swap=word_swap, long_swap=long_swap
        )
        assert decoded == value

    def test_encode_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown data type"):
            ModbusDriver.encode_value(1, "uint128")

    def test_decode_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown data type"):
            ModbusDriver.decode_registers([0], "uint128")


# ============ Lock ============


class TestLock:
    def test_lock_is_reentrant_and_serializes_ops(self, driver):
        # Holding the lock lets the same thread issue ops inside the with-block (RLock).
        with driver.lock():
            assert driver.read_holding_registers(100, 1) == [TEST_DATA["holding_uint16"]]

    def test_lock_returns_same_object(self, driver):
        assert driver.lock() is driver.lock()


# ============ Shared Ownership ============


class TestSharedOwnership:
    def test_two_holders_share_one_client_last_release_closes(self, modbus_server):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
        a, b = object(), object()

        first = drv.acquire(a)
        second = drv.acquire(b)

        assert first is True
        assert second is False
        assert drv.is_open

        drv.release(a)
        assert drv.is_open  # b still holds it

        cb = Mock()
        drv.release(b, on_last_release=cb)

        cb.assert_called_once()
        assert not drv.is_open

    def test_one_connect_across_two_acquires(self):
        with patch("pymodbus.client.ModbusTcpClient") as mock_cls:
            mock_cls.return_value.connect.return_value = True
            drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=1))
            a, b = object(), object()

            drv.acquire(a)
            drv.acquire(b)

            mock_cls.return_value.connect.assert_called_once()
            drv.release(a)
            drv.release(b)

    def test_release_by_non_holder_is_noop(self, modbus_server):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
        a, stranger = object(), object()
        drv.acquire(a)

        drv.release(stranger)

        assert drv.is_open
        drv.release(a)

    def test_direct_close_while_owned_declines_and_logs(self, modbus_server, caplog):
        drv = ModbusDriver(TCPConnection(host="127.0.0.1", port=TEST_PORT))
        a = object()
        drv.acquire(a)

        with caplog.at_level(logging.WARNING, logger="instro.lib.transports.ownership"):
            drv.close()

        assert drv.is_open
        assert len(caplog.records) == 1
        drv.release(a)


# ============ Connection Configs ============


class TestConnectionConfigs:
    def test_tcp_defaults(self):
        conn = TCPConnection(host="192.168.1.10")
        assert (conn.transport, conn.port, conn.unit_id) == ("tcp", 502, 1)

    def test_rtu_defaults(self):
        conn = RTUConnection(port="/dev/ttyUSB0")
        assert (conn.transport, conn.baudrate, conn.parity) == ("rtu", 9600, "N")


class TestRTUOpen:
    """RTU/serial open path, mocked (no serial hardware in CI)."""

    def test_open_builds_serial_client_from_connection(self):
        conn = RTUConnection(port="/dev/ttyUSB0", baudrate=19200, parity="E", stopbits=2, bytesize=7, unit_id=3)
        with patch("pymodbus.client.ModbusSerialClient") as mock_cls:
            mock_cls.return_value.connect.return_value = True
            drv = ModbusDriver(conn)
            drv.open()
            assert drv.is_open
            assert drv.unit_id == 3
            mock_cls.assert_called_once_with(
                port="/dev/ttyUSB0", baudrate=19200, parity="E", stopbits=2, bytesize=7, timeout=conn.timeout
            )
            drv.close()
            mock_cls.return_value.close.assert_called()

    def test_connect_failure_raises_with_serial_port(self):
        with patch("pymodbus.client.ModbusSerialClient") as mock_cls:
            mock_cls.return_value.connect.return_value = False
            drv = ModbusDriver(RTUConnection(port="/dev/ttyUSB0"))
            with pytest.raises(ConnectionError, match="/dev/ttyUSB0"):
                drv.open()
            assert not drv.is_open
            mock_cls.return_value.close.assert_called_once()
