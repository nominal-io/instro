"""Tests for the Modbus transports against a simulated Modbus TCP server."""

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
from pymodbus.framer import FramerType
from pymodbus.server import StartAsyncTcpServer

from instro.lib.exceptions import UnknownHolderError
from instro.lib.transports.modbus import (
    ModbusRTUTransport,
    ModbusTCPTransport,
    ModbusTransport,
    decode_registers,
    encode_value,
    register_count,
)

TEST_PORT = 5031

# A second simulated unit answering on the same socket, holding a different value at address 100.
SECOND_UNIT = 2
SECOND_UNIT_HOLDING = 11111

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
    # A second unit on the same listening socket, so tests can prove unit_id actually
    # routes rather than being ignored: same address, deliberately different value.
    second_hr = [0] * 200
    second_hr[100] = SECOND_UNIT_HOLDING
    second_store = ModbusDeviceContext(hr=ModbusSequentialDataBlock(0, [0] + second_hr))

    return ModbusServerContext(devices={1: store, SECOND_UNIT: second_store}, single=False)


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
def bus(modbus_server):
    """An open ModbusTCPTransport against the sim server."""
    transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
    transport.open()
    yield transport
    transport.close()


# ============ Lifecycle ============


class TestLifecycle:
    def test_not_open_before_open(self):
        assert not ModbusTCPTransport(host="h", port=1).is_open

    def test_open_close_toggles_is_open(self, modbus_server):
        transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
        assert not transport.is_open
        transport.open()
        assert transport.is_open
        transport.close()
        assert not transport.is_open

    def test_open_is_idempotent(self, modbus_server):
        transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
        transport.open()
        transport.open()  # no raise, no reconnect churn
        assert transport.is_open
        transport.close()

    def test_close_is_idempotent(self, modbus_server):
        transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
        transport.open()
        transport.close()
        transport.close()  # no raise
        assert not transport.is_open

    def test_del_closes_open_transport(self, modbus_server):
        transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
        transport.open()
        transport.__del__()  # best-effort close on GC
        assert not transport.is_open

    def test_del_on_unopened_is_safe(self):
        ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT).__del__()  # no raise

    def test_reconnects_after_transport_error(self, modbus_server):
        transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
        transport.open()
        try:
            assert transport.read_holding_registers(100, 1, unit_id=1) == [TEST_DATA["holding_uint16"]]

            # Force one transport error at the pymodbus client layer; _modbus_op should
            # drop the dead socket and re-raise.
            with patch.object(transport._client, "read_holding_registers", side_effect=ConnectionException("boom")):
                with pytest.raises(ConnectionException):
                    transport.read_holding_registers(100, 1, unit_id=1)

            # The next real op reconnects (pymodbus connect() rebuilds the socket) and succeeds.
            assert transport.read_holding_registers(100, 1, unit_id=1) == [TEST_DATA["holding_uint16"]]
        finally:
            transport.close()


# ============ Raw Function-Code Ops ============


class TestRawOps:
    def test_read_holding_registers(self, bus):
        assert bus.read_holding_registers(100, 1, unit_id=1) == [TEST_DATA["holding_uint16"]]

    def test_read_input_registers(self, bus):
        assert bus.read_input_registers(0, 1, unit_id=1) == [TEST_DATA["input_uint16"]]

    def test_read_coils(self, bus):
        assert bus.read_coils(0, 2, unit_id=1) == [TEST_DATA["coil_1"], TEST_DATA["coil_2"]]

    def test_read_discrete_inputs(self, bus):
        assert bus.read_discrete_inputs(0, 2, unit_id=1) == [TEST_DATA["discrete_1"], TEST_DATA["discrete_2"]]

    def test_write_holding_register_readback(self, bus):
        bus.write_holding_register(150, 4242, unit_id=1)
        assert bus.read_holding_registers(150, 1, unit_id=1) == [4242]

    def test_write_holding_registers_readback(self, bus):
        bus.write_holding_registers(160, [11, 22, 33], unit_id=1)
        assert bus.read_holding_registers(160, 3, unit_id=1) == [11, 22, 33]

    def test_write_coil_readback(self, bus):
        bus.write_coil(5, True, unit_id=1)
        assert bus.read_coils(5, 1, unit_id=1) == [True]

    def test_write_coils_readback(self, bus):
        bus.write_coils(6, [True, False, True], unit_id=1)
        assert bus.read_coils(6, 3, unit_id=1) == [True, False, True]

    def test_read_beyond_datastore_raises_modbus_error(self, bus):
        # Valid Modbus address, but past the sim datastore -> device returns IllegalDataAddress.
        with pytest.raises(RuntimeError, match="Modbus error"):
            bus.read_holding_registers(500, 1, unit_id=1)


# ============ Typed Access ============


class TestTypedAccess:
    def test_read_typed_input_uint16(self, bus):
        assert bus.read_typed("input", 0, "uint16", unit_id=1) == TEST_DATA["input_uint16"]

    def test_read_typed_input_int16(self, bus):
        assert bus.read_typed("input", 1, "int16", unit_id=1) == TEST_DATA["input_int16"]

    def test_read_typed_input_uint32(self, bus):
        assert bus.read_typed("input", 10, "uint32", unit_id=1) == TEST_DATA["input_uint32"]

    def test_read_typed_input_float32(self, bus):
        assert bus.read_typed("input", 30, "float32", unit_id=1) == pytest.approx(TEST_DATA["input_float32"], rel=1e-5)

    def test_read_typed_input_float64(self, bus):
        assert bus.read_typed("input", 40, "float64", unit_id=1) == pytest.approx(TEST_DATA["input_float64"], rel=1e-10)

    def test_read_typed_holding_word_swap(self, bus):
        assert bus.read_typed("holding", 130, "uint32", unit_id=1, word_swap=True) == TEST_DATA["holding_word_swap"]

    def test_read_typed_coil_returns_bool(self, bus):
        result = bus.read_typed("coil", 1, "bool", unit_id=1)
        assert result is True

    def test_read_typed_discrete_returns_bool(self, bus):
        result = bus.read_typed("discrete", 1, "bool", unit_id=1)
        assert result is False

    def test_read_typed_coil_rejects_non_bool_dtype(self, bus):
        with pytest.raises(ValueError, match="single-bit; data_type must be 'bool'"):
            bus.read_typed("coil", 1, "uint16", unit_id=1)

    def test_read_typed_discrete_rejects_non_bool_dtype(self, bus):
        with pytest.raises(ValueError, match="single-bit; data_type must be 'bool'"):
            bus.read_typed("discrete", 1, "uint16", unit_id=1)

    def test_read_typed_unknown_register_type_raises(self, bus):
        with pytest.raises(ValueError, match="Unknown register type"):
            bus.read_typed("bogus", 0, "uint16", unit_id=1)

    def test_write_typed_holding_single_register_readback(self, bus):
        bus.write_typed("holding", 170, 4321, "uint16", unit_id=1)
        assert bus.read_typed("holding", 170, "uint16", unit_id=1) == 4321

    def test_write_typed_holding_multi_register_readback(self, bus):
        bus.write_typed("holding", 172, -123456789, "int32", unit_id=1)
        assert bus.read_typed("holding", 172, "int32", unit_id=1) == -123456789

    def test_write_typed_holding_float_roundtrip(self, bus):
        bus.write_typed("holding", 176, 3.14159, "float32", unit_id=1)
        assert bus.read_typed("holding", 176, "float32", unit_id=1) == pytest.approx(3.14159, rel=1e-5)

    def test_write_typed_coil_readback(self, bus):
        bus.write_typed("coil", 8, True, "bool", unit_id=1)
        assert bus.read_typed("coil", 8, "bool", unit_id=1) is True

    def test_write_typed_coil_rejects_non_bool_dtype(self, bus):
        with pytest.raises(ValueError, match="single-bit; data_type must be 'bool'"):
            bus.write_typed("coil", 8, True, "uint16", unit_id=1)

    def test_write_typed_coil_rejects_non_bool_value(self, bus):
        # Strict: no numeric coercion. Even 1/0 must be passed as real booleans.
        with pytest.raises(ValueError, match="coil writes require a bool value"):
            bus.write_typed("coil", 8, 1, "bool", unit_id=1)

    def test_write_typed_read_only_raises(self, bus):
        with pytest.raises(ValueError, match="read-only"):
            bus.write_typed("input", 0, 1, "uint16", unit_id=1)


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
        assert register_count(data_type) == expected

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
        encoded = encode_value(value, data_type)
        assert decode_registers(encoded, data_type) == value

    @pytest.mark.parametrize("data_type,rel", [("float32", 1e-6), ("float64", 1e-12)])
    def test_float_roundtrip(self, data_type, rel):
        encoded = encode_value(3.14159265, data_type)
        assert decode_registers(encoded, data_type) == pytest.approx(3.14159265, rel=rel)

    @pytest.mark.parametrize("word_swap", [False, True])
    @pytest.mark.parametrize("byte_swap", [False, True])
    def test_swap_roundtrip(self, word_swap, byte_swap):
        encoded = encode_value(0xDEADBEEF, "uint32", byte_swap=byte_swap, word_swap=word_swap)
        decoded = decode_registers(encoded, "uint32", byte_swap=byte_swap, word_swap=word_swap)
        assert decoded == 0xDEADBEEF

    @pytest.mark.parametrize("long_swap", [False, True])
    @pytest.mark.parametrize("word_swap", [False, True])
    @pytest.mark.parametrize("byte_swap", [False, True])
    def test_swap_roundtrip_64bit(self, long_swap, word_swap, byte_swap):
        # long_swap only applies to 64-bit types; exercise it across every swap combination.
        value = 0x1337BEEFCAFEBABE
        encoded = encode_value(value, "uint64", byte_swap=byte_swap, word_swap=word_swap, long_swap=long_swap)
        decoded = decode_registers(encoded, "uint64", byte_swap=byte_swap, word_swap=word_swap, long_swap=long_swap)
        assert decoded == value

    def test_encode_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown data type"):
            encode_value(1, "uint128")

    def test_decode_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown data type"):
            decode_registers([0], "uint128")

    def test_register_count_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown data type"):
            register_count("uint128")


# ============ Lock ============


class TestLock:
    def test_lock_is_reentrant_and_serializes_ops(self, bus):
        # Holding the lock lets the same thread issue ops inside the with-block (RLock).
        with bus.lock():
            assert bus.read_holding_registers(100, 1, unit_id=1) == [TEST_DATA["holding_uint16"]]

    def test_lock_returns_same_object(self, bus):
        assert bus.lock() is bus.lock()


# ============ Shared Ownership ============


class TestSharedOwnership:
    def test_two_holders_share_one_client_last_close_closes(self, modbus_server):
        transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
        a, b = object(), object()

        first = transport.open(a)
        second = transport.open(b)

        assert first is True
        assert second is False
        assert transport.is_open

        transport.close(a)
        assert transport.is_open  # b still holds it

        transport.close(b)

        assert not transport.is_open

    def test_one_connect_across_two_holder_opens(self):
        with patch("instro.lib.transports.modbus.ModbusTcpClient") as mock_cls:
            mock_cls.return_value.connect.return_value = True
            transport = ModbusTCPTransport(host="127.0.0.1", port=1)
            a, b = object(), object()

            transport.open(a)
            transport.open(b)

            mock_cls.return_value.connect.assert_called_once()
            transport.close(a)
            transport.close(b)

    def test_close_by_non_holder_raises(self, modbus_server):
        transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
        a, stranger = object(), object()
        transport.open(a)

        with pytest.raises(UnknownHolderError, match="does not own this"):
            transport.close(stranger)

        assert transport.is_open
        transport.close(a)

    def test_bare_close_while_owned_declines_and_logs(self, modbus_server, caplog):
        transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
        a = object()
        transport.open(a)

        with caplog.at_level(logging.WARNING, logger="instro.lib.transports.transport_base"):
            transport.close()

        assert transport.is_open
        assert len(caplog.records) == 1
        transport.close(a)


# ============ ModbusTransport ============


class TestTransportConstruction:
    def test_abstract_base_cannot_be_constructed(self):
        with pytest.raises(TypeError):
            ModbusTransport()  # type: ignore[abstract]

    def test_no_unit_id_on_the_transport_surface(self):
        with pytest.raises(TypeError):
            ModbusTCPTransport(host="h", unit_id=5)  # type: ignore[call-arg]

    def test_tcp_defaults_reach_the_client(self):
        with patch("instro.lib.transports.modbus.ModbusTcpClient") as mock_cls:
            mock_cls.return_value.connect.return_value = True
            ModbusTCPTransport(host="192.168.1.10").open()
            mock_cls.assert_called_once_with(host="192.168.1.10", port=502, timeout=3.0)

    def test_rtu_defaults_reach_the_client(self):
        with patch("instro.lib.transports.modbus.ModbusSerialClient") as mock_cls:
            mock_cls.return_value.connect.return_value = True
            ModbusRTUTransport(port="/dev/ttyUSB0").open()
            mock_cls.assert_called_once_with(
                port="/dev/ttyUSB0",
                framer=FramerType.RTU,
                baudrate=9600,
                parity="N",
                stopbits=1,
                bytesize=8,
                timeout=3.0,
            )

    def test_rtu_ascii_framer_reaches_the_client(self):
        with patch("instro.lib.transports.modbus.ModbusSerialClient") as mock_cls:
            mock_cls.return_value.connect.return_value = True
            ModbusRTUTransport(port="/dev/ttyUSB0", framer="ascii").open()
            mock_cls.assert_called_once_with(
                port="/dev/ttyUSB0",
                framer=FramerType.ASCII,
                baudrate=9600,
                parity="N",
                stopbits=1,
                bytesize=8,
                timeout=3.0,
            )

    def test_rtu_rejects_unknown_framer(self):
        with pytest.raises(ValueError, match="framer"):
            ModbusRTUTransport(port="/dev/ttyUSB0", framer="binary")  # type: ignore[arg-type]

    @pytest.mark.parametrize("port", [0, 65536])
    def test_tcp_rejects_out_of_range_port(self, port):
        with pytest.raises(ValueError, match="port"):
            ModbusTCPTransport(host="h", port=port)

    def test_tcp_rejects_non_positive_timeout(self):
        with pytest.raises(ValueError, match="timeout"):
            ModbusTCPTransport(host="h", timeout=0)

    def test_rtu_rejects_unknown_parity(self):
        # mypy rejects this statically too; the runtime check is for dict-fed callers.
        with pytest.raises(ValueError, match="parity"):
            ModbusRTUTransport(port="/dev/ttyUSB0", parity="X")  # type: ignore[arg-type]

    def test_rtu_rejects_unknown_stopbits(self):
        with pytest.raises(ValueError, match="stopbits"):
            ModbusRTUTransport(port="/dev/ttyUSB0", stopbits=3)  # type: ignore[arg-type]

    def test_rtu_rejects_unknown_bytesize(self):
        with pytest.raises(ValueError, match="bytesize"):
            ModbusRTUTransport(port="/dev/ttyUSB0", bytesize=9)  # type: ignore[arg-type]


class TestPerCallAddressing:
    def test_unit_id_routes_to_the_addressed_unit(self, bus):
        assert bus.read_holding_registers(100, 1, unit_id=1) == [TEST_DATA["holding_uint16"]]
        assert bus.read_holding_registers(100, 1, unit_id=SECOND_UNIT) == [SECOND_UNIT_HOLDING]

    def test_wire_op_requires_unit_id(self, bus):
        with pytest.raises(TypeError):
            bus.read_holding_registers(100, 1)  # type: ignore[call-arg]

    def test_read_typed_requires_unit_id(self, bus):
        with pytest.raises(TypeError):
            bus.read_typed("holding", 100, "uint16")  # type: ignore[call-arg]

    def test_op_before_open_raises_before_the_missing_unit_id(self):
        # The connectivity check fires first, so the TypeError gate above only holds once open.
        transport = ModbusTCPTransport(host="127.0.0.1", port=TEST_PORT)
        with pytest.raises(RuntimeError, match="not connected"):
            transport.read_holding_registers(100, 1, unit_id=1)


class TestUnitIdValidation:
    @pytest.mark.parametrize("unit_id", [-1, 256])
    def test_check_unit_id_rejects_out_of_range(self, unit_id):
        with pytest.raises(ValueError, match="unit_id"):
            ModbusTCPTransport(host="h").check_unit_id(unit_id)

    @pytest.mark.parametrize("unit_id", [248, 255])
    def test_rtu_rejects_reserved_addresses(self, unit_id):
        # Modbus over Serial Line spec 2.2: 248-255 are reserved, not individual slave addresses.
        with pytest.raises(ValueError, match="unit_id"):
            ModbusRTUTransport(port="/dev/ttyUSB0").check_unit_id(unit_id)

    @pytest.mark.parametrize("unit_id", [0, 247])
    def test_rtu_accepts_boundary_addresses(self, unit_id):
        assert ModbusRTUTransport(port="/dev/ttyUSB0").check_unit_id(unit_id) == unit_id

    def test_check_unit_id_warns_on_broadcast_address(self, caplog):
        # unit_id=0 is valid (the Modbus broadcast address), but easy to hit by accident,
        # so it gets a log warning rather than passing through silently like any other address.
        with caplog.at_level(logging.WARNING, logger="instro.lib.transports.modbus"):
            ModbusTCPTransport(host="h").check_unit_id(0)
        assert len(caplog.records) == 1
        assert "broadcast" in caplog.records[0].message

    @pytest.mark.parametrize("unit_id", [1, 255])
    def test_check_unit_id_does_not_warn_on_non_broadcast_address(self, unit_id, caplog):
        with caplog.at_level(logging.WARNING, logger="instro.lib.transports.modbus"):
            ModbusTCPTransport(host="h").check_unit_id(unit_id)
        assert len(caplog.records) == 0

    def test_writes_reach_only_the_addressed_unit(self, bus):
        bus.write_holding_register(101, 777, unit_id=SECOND_UNIT)
        assert bus.read_holding_registers(101, 1, unit_id=SECOND_UNIT) == [777]
        assert bus.read_holding_registers(101, 1, unit_id=1) == [0]

    @pytest.mark.parametrize("unit_id", [-1, 256])
    def test_raw_wire_op_rejects_out_of_range_unit_id(self, bus, unit_id):
        # No ModbusDevice involved: the transport itself must guard this on every raw op.
        with pytest.raises(ValueError, match="unit_id"):
            bus.read_holding_registers(100, 1, unit_id=unit_id)

    @pytest.mark.parametrize("unit_id", [248, 255])
    def test_rtu_raw_wire_op_rejects_reserved_unit_id(self, unit_id):
        with patch("instro.lib.transports.modbus.ModbusSerialClient") as mock_cls:
            mock_cls.return_value.connect.return_value = True
            transport = ModbusRTUTransport(port="/dev/ttyUSB0")
            transport.open()
            with pytest.raises(ValueError, match="unit_id"):
                transport.read_holding_registers(100, 1, unit_id=unit_id)
            transport.close()


class TestTransportConnect:
    def test_tcp_connect_failure_names_host_and_port(self):
        transport = ModbusTCPTransport(host="127.0.0.1", port=1, timeout=0.2)
        with pytest.raises(ConnectionError, match="127.0.0.1:1"):
            transport.open()
        assert not transport.is_open

    def test_connect_failure_closes_the_half_open_client(self):
        with patch("instro.lib.transports.modbus.ModbusTcpClient") as mock_cls:
            mock_cls.return_value.connect.return_value = False
            with pytest.raises(ConnectionError):
                ModbusTCPTransport(host="h", port=502).open()
            mock_cls.return_value.close.assert_called_once()

    def test_rtu_builds_serial_client_from_its_own_fields(self):
        with patch("instro.lib.transports.modbus.ModbusSerialClient") as mock_cls:
            mock_cls.return_value.connect.return_value = True
            transport = ModbusRTUTransport(
                port="/dev/ttyUSB0", baudrate=19200, parity="E", stopbits=2, bytesize=7, timeout=1.5
            )
            transport.open()

            assert transport.is_open
            mock_cls.assert_called_once_with(
                port="/dev/ttyUSB0",
                framer=FramerType.RTU,
                baudrate=19200,
                parity="E",
                stopbits=2,
                bytesize=7,
                timeout=1.5,
            )
            transport.close()

    def test_rtu_opens_one_serial_client_across_two_holders(self):
        # pymodbus opens serial exclusive=True, so a second construction is the bug this fixes.
        with patch("instro.lib.transports.modbus.ModbusSerialClient") as mock_cls:
            mock_cls.return_value.connect.return_value = True
            transport = ModbusRTUTransport(port="/dev/ttyUSB0")
            holder_a, holder_b = object(), object()

            transport.open(holder_a)
            transport.open(holder_b)

            assert mock_cls.call_count == 1
            transport.close(holder_a)
            transport.close(holder_b)

    def test_rtu_connect_failure_names_the_serial_port(self):
        with patch("instro.lib.transports.modbus.ModbusSerialClient") as mock_cls:
            mock_cls.return_value.connect.return_value = False
            transport = ModbusRTUTransport(port="/dev/ttyUSB0")
            with pytest.raises(ConnectionError, match="/dev/ttyUSB0"):
                transport.open()
            assert not transport.is_open
            mock_cls.return_value.close.assert_called_once()
