"""Modbus transport driver. Wraps pymodbus; callers own register maps, this owns I/O, framing, and locking."""

from __future__ import annotations

import functools
import struct
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
from pymodbus.exceptions import ConnectionException as PymodbusConnectionException

from instro.lib.transports.transport_base import TransportBase

if TYPE_CHECKING:
    from pymodbus.client import ModbusSerialClient, ModbusTcpClient


class TCPConnection(BaseModel):
    """Modbus TCP connection configuration."""

    transport: Literal["tcp"] = "tcp"
    host: str
    port: int = Field(default=502, ge=1, le=65535)
    unit_id: int = Field(default=1, ge=0, le=255)
    timeout: float = Field(default=3.0, gt=0, description="Response timeout in seconds")


class RTUConnection(BaseModel):
    """Modbus RTU (serial) connection configuration."""

    transport: Literal["rtu"] = "rtu"
    port: str  # e.g., "/dev/ttyUSB0" (Linux), "/dev/cu.usbserial-1234" (macOS), "COM3" (Windows)
    baudrate: int = 9600
    parity: Literal["N", "E", "O"] = "N"
    stopbits: Literal[1, 2] = 1
    bytesize: Literal[5, 6, 7, 8] = 8
    unit_id: int = Field(default=1, ge=0, le=255)
    timeout: float = Field(default=3.0, gt=0, description="Response timeout in seconds")


ConnectionType = TCPConnection | RTUConnection

# Modbus protocol vocabulary. Single source of truth; ``instro.modbus.types`` re-exports these.
RegisterType = Literal["holding", "input", "coil", "discrete"]
DataType = Literal["uint16", "int16", "uint32", "int32", "uint64", "int64", "float32", "float64", "bool"]


def _modbus_op(fn):
    """Acquire the lock, run the operation, and clear dead sockets on failure.

    The pymodbus sync client does not auto-reconnect between operations
    (``reconnect_delay`` is async-only). It does call ``connect()`` before every
    operation, creating a new socket when ``self._client.socket is None``. So on
    a transport error we close the dead socket and re-raise — the next call gets
    a fresh connection.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            if self._client is None:
                raise RuntimeError("Modbus client not connected. Call open() first.")
            try:
                return fn(self, *args, **kwargs)
            except (OSError, ConnectionError, PymodbusConnectionException):
                self._client.close()
                raise

    return wrapper


class ModbusDriver(TransportBase):
    """Transport for Modbus TCP/RTU instruments. Composed by concrete drivers, not extended.

    Supports shared ownership: multiple drivers can hold the same connection by passing
    themselves as the holder to :meth:`open`/:meth:`close`, and it closes only when the
    last owner closes it. Thread-safe at the I/O level via an internal lock; use
    :meth:`lock` to keep a multi-step Modbus sequence atomic. Raw
    function-code ops return/accept the 16-bit register words or coil bits on the
    wire; :meth:`read_typed` / :meth:`write_typed` add typed encode/decode across registers.
    """

    def __init__(self, connection: TCPConnection | RTUConnection) -> None:
        """Construct from a ``TCPConnection`` or ``RTUConnection``."""
        super().__init__()
        self._connection = connection
        self._client: ModbusTcpClient | ModbusSerialClient | None = None

    @property
    def is_open(self) -> bool:
        """Whether the client has been opened and not closed. Stays ``True`` across a dropped socket, which the next op reconnects."""
        return self._client is not None

    @property
    def unit_id(self) -> int:
        """Modbus unit/slave ID from the connection config."""
        return self._connection.unit_id

    def _open_session(self) -> None:
        """Open the Modbus TCP/RTU connection. Idempotent. Called by open()."""
        with self._lock:
            if self._client is not None:
                return

            connection = self._connection
            if isinstance(connection, TCPConnection):
                from pymodbus.client import ModbusTcpClient

                client: ModbusTcpClient | ModbusSerialClient = ModbusTcpClient(
                    host=connection.host,
                    port=connection.port,
                    timeout=connection.timeout,
                )
            elif isinstance(connection, RTUConnection):
                from pymodbus.client import ModbusSerialClient

                client = ModbusSerialClient(
                    port=connection.port,
                    baudrate=connection.baudrate,
                    parity=connection.parity,
                    stopbits=connection.stopbits,
                    bytesize=connection.bytesize,
                    timeout=connection.timeout,
                )
            else:
                raise ValueError(f"Unknown connection type: {type(connection)}")

            if not client.connect():
                target = (
                    f"{connection.host}:{connection.port}" if isinstance(connection, TCPConnection) else connection.port
                )
                client.close()
                raise ConnectionError(f"Failed to connect to Modbus device at {target}")

            self._client = client

    def _teardown_session(self) -> None:
        """Tear down the Modbus session."""
        if self._client is not None:
            self._client.close()
            self._client = None

    @_modbus_op
    def read_holding_registers(self, address: int, count: int) -> list[int]:
        """Read holding registers by address (FC03)."""
        assert self._client is not None
        result = self._client.read_holding_registers(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"reading holding registers at addr={address}", result))
        return list(result.registers)

    @_modbus_op
    def read_input_registers(self, address: int, count: int) -> list[int]:
        """Read input registers by address (FC04)."""
        assert self._client is not None
        result = self._client.read_input_registers(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"reading input registers at addr={address}", result))
        return list(result.registers)

    @_modbus_op
    def write_holding_register(self, address: int, value: int) -> None:
        """Write a single holding register by address (FC06)."""
        assert self._client is not None
        result = self._client.write_register(address, value, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"writing holding register at addr={address}", result))

    @_modbus_op
    def write_holding_registers(self, address: int, values: list[int]) -> None:
        """Write multiple holding registers by address (FC16)."""
        assert self._client is not None
        result = self._client.write_registers(address, values, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"writing holding registers at addr={address}", result))

    @_modbus_op
    def read_coils(self, address: int, count: int) -> list[bool]:
        """Read coils by address (FC01)."""
        assert self._client is not None
        result = self._client.read_coils(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"reading coils at addr={address}", result))
        return list(result.bits[:count])

    @_modbus_op
    def write_coil(self, address: int, value: bool) -> None:
        """Write a single coil by address (FC05)."""
        assert self._client is not None
        result = self._client.write_coil(address, value, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"writing coil at addr={address}", result))

    @_modbus_op
    def write_coils(self, address: int, values: list[bool]) -> None:
        """Write multiple coils by address (FC15)."""
        assert self._client is not None
        result = self._client.write_coils(address, values, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"writing coils at addr={address}", result))

    @_modbus_op
    def read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        """Read discrete inputs by address (FC02)."""
        assert self._client is not None
        result = self._client.read_discrete_inputs(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"reading discrete inputs at addr={address}", result))
        return list(result.bits[:count])

    def read_typed(
        self,
        register_type: RegisterType,
        address: int,
        data_type: DataType,
        *,
        byte_swap: bool = False,
        word_swap: bool = False,
        long_swap: bool = False,
    ) -> int | float | bool:
        """Read ``address`` as ``data_type``, dispatching by ``register_type`` and decoding across registers.

        Coils and discrete inputs are single-bit, so ``data_type`` must be ``"bool"`` and the read returns a ``bool``.
        """
        match register_type:
            case "holding":
                raw_regs = self.read_holding_registers(address, self.register_count(data_type))
            case "input":
                raw_regs = self.read_input_registers(address, self.register_count(data_type))
            case "coil":
                if data_type != "bool":
                    raise ValueError(f"coil registers are single-bit; data_type must be 'bool', got '{data_type}'")
                return bool(self.read_coils(address, 1)[0])
            case "discrete":
                if data_type != "bool":
                    raise ValueError(f"discrete registers are single-bit; data_type must be 'bool', got '{data_type}'")
                return bool(self.read_discrete_inputs(address, 1)[0])
            case _:
                raise ValueError(f"Unknown register type: {register_type}")
        return self.decode_registers(raw_regs, data_type, byte_swap, word_swap, long_swap)

    def write_typed(
        self,
        register_type: RegisterType,
        address: int,
        value: int | float | bool,
        data_type: DataType,
        *,
        byte_swap: bool = False,
        word_swap: bool = False,
        long_swap: bool = False,
    ) -> None:
        """Encode ``value`` as ``data_type`` and write it to ``address``, dispatching by ``register_type``.

        Coils are single-bit, so ``data_type`` must be ``"bool"`` and ``value`` must be a ``bool`` (no numeric coercion).
        """
        match register_type:
            case "holding":
                encoded = self.encode_value(value, data_type, byte_swap, word_swap, long_swap)
                if len(encoded) == 1:
                    self.write_holding_register(address, encoded[0])
                else:
                    self.write_holding_registers(address, encoded)
            case "coil":
                if data_type != "bool":
                    raise ValueError(f"coil registers are single-bit; data_type must be 'bool', got '{data_type}'")
                if not isinstance(value, bool):
                    raise ValueError(f"coil writes require a bool value, got {type(value).__name__} {value!r}")
                self.write_coil(address, value)
            case "input" | "discrete":
                raise ValueError(f"Cannot write to read-only register type: {register_type}")
            case _:
                raise ValueError(f"Unknown register type: {register_type}")

    @staticmethod
    def register_count(data_type: DataType) -> int:
        """Number of 16-bit registers ``data_type`` spans (uint16→1, uint32→2, uint64→4)."""
        match data_type:
            case "uint16" | "int16" | "bool":
                return 1
            case "uint32" | "int32" | "float32":
                return 2
            case "uint64" | "int64" | "float64":
                return 4
            case _:
                return 1

    @staticmethod
    def decode_registers(
        registers: list[int],
        data_type: DataType,
        byte_swap: bool = False,
        word_swap: bool = False,
        long_swap: bool = False,
    ) -> int | float:
        """Decode raw 16-bit registers into a typed value, applying byte/word/long swaps as configured."""
        raw_bytes = b"".join(reg.to_bytes(2, "big") for reg in registers)

        if byte_swap:
            raw_bytes = b"".join(raw_bytes[i : i + 2][::-1] for i in range(0, len(raw_bytes), 2))

        if word_swap and len(raw_bytes) >= 4:
            swapped = bytearray()
            for i in range(0, len(raw_bytes), 4):
                chunk = raw_bytes[i : i + 4]
                if len(chunk) == 4:
                    swapped.extend(chunk[2:4] + chunk[0:2])
                else:
                    swapped.extend(chunk)
            raw_bytes = bytes(swapped)

        if long_swap and len(raw_bytes) == 8:
            raw_bytes = raw_bytes[4:8] + raw_bytes[0:4]

        match data_type:
            case "uint16":
                return struct.unpack(">H", raw_bytes)[0]
            case "int16":
                return struct.unpack(">h", raw_bytes)[0]
            case "uint32":
                return struct.unpack(">I", raw_bytes)[0]
            case "int32":
                return struct.unpack(">i", raw_bytes)[0]
            case "uint64":
                return struct.unpack(">Q", raw_bytes)[0]
            case "int64":
                return struct.unpack(">q", raw_bytes)[0]
            case "float32":
                return float(struct.unpack(">f", raw_bytes)[0])
            case "float64":
                return float(struct.unpack(">d", raw_bytes)[0])
            case "bool":
                return int(registers[0] != 0)
            case _:
                raise ValueError(f"Unknown data type: {data_type}")

    @staticmethod
    def encode_value(
        value: int | float | bool,
        data_type: DataType,
        byte_swap: bool = False,
        word_swap: bool = False,
        long_swap: bool = False,
    ) -> list[int]:
        """Encode a typed value into 16-bit registers, applying byte/word/long swaps as configured."""
        match data_type:
            case "uint16":
                raw_bytes = struct.pack(">H", round(value))
            case "int16":
                raw_bytes = struct.pack(">h", round(value))
            case "uint32":
                raw_bytes = struct.pack(">I", round(value))
            case "int32":
                raw_bytes = struct.pack(">i", round(value))
            case "uint64":
                raw_bytes = struct.pack(">Q", round(value))
            case "int64":
                raw_bytes = struct.pack(">q", round(value))
            case "float32":
                raw_bytes = struct.pack(">f", float(value))
            case "float64":
                raw_bytes = struct.pack(">d", float(value))
            case "bool":
                return [1 if value else 0]
            case _:
                raise ValueError(f"Unknown data type: {data_type}")

        if long_swap and len(raw_bytes) == 8:
            raw_bytes = raw_bytes[4:8] + raw_bytes[0:4]

        if word_swap and len(raw_bytes) >= 4:
            swapped = bytearray()
            for i in range(0, len(raw_bytes), 4):
                chunk = raw_bytes[i : i + 4]
                if len(chunk) == 4:
                    swapped.extend(chunk[2:4] + chunk[0:2])
                else:
                    swapped.extend(chunk)
            raw_bytes = bytes(swapped)

        if byte_swap:
            raw_bytes = b"".join(raw_bytes[i : i + 2][::-1] for i in range(0, len(raw_bytes), 2))

        return [int.from_bytes(raw_bytes[i : i + 2], "big") for i in range(0, len(raw_bytes), 2)]

    @staticmethod
    def _format_modbus_error(operation: str, result: object) -> str:
        """Format a Modbus error response, including the standard exception-code name (FC 0x01–0x0B)."""
        exception_codes = {
            1: "IllegalFunction",
            2: "IllegalDataAddress",
            3: "IllegalDataValue",
            4: "SlaveDeviceFailure",
            5: "Acknowledge",
            6: "SlaveDeviceBusy",
            8: "MemoryParityError",
            10: "GatewayPathUnavailable",
            11: "GatewayNoResponse",
        }
        code = getattr(result, "exception_code", 0)
        name = exception_codes.get(code, "Unknown")
        if code:
            return f"Modbus error {operation}: {name} (0x{code:02X})"
        return f"Modbus error {operation}: {result}"
