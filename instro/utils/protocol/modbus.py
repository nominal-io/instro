"""Modbus transport driver.

`ModbusDriver` wraps pymodbus for concrete Modbus instrument drivers. It
deliberately stays at the transport layer: callers choose register addresses
and handle encoding, decoding, and scaling.
"""

from __future__ import annotations

import contextlib
import functools
import threading
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
from pymodbus.exceptions import ConnectionException as PymodbusConnectionException

if TYPE_CHECKING:
    from pymodbus.client import ModbusSerialClient, ModbusTcpClient


# ── Connection configuration ──────────────────────────────────────────────────


class ModbusConnectionConfigBase(BaseModel):
    unit_id: int = Field(default=1, ge=0, le=255)
    timeout: float = Field(default=3.0, gt=0, description="Response timeout in seconds")

    def _format_target(self) -> str:
        raise NotImplementedError(
            f"Connection information requested from base class which is only aware of the unit_id [{self.unit_id}]"
        )


class TCPConnectionConfig(ModbusConnectionConfigBase):
    """Modbus TCP connection configuration."""

    transport: Literal["tcp"] = "tcp"
    host: str
    port: int = Field(default=502, ge=1, le=65535)

    def _format_target(self) -> str:
        return f"{self.host}:{self.port}" + (f"@{self.unit_id}" if self.unit_id is not None else "")

    @classmethod
    def _parse_tcp_string(cls, s: str) -> TCPConnectionConfig:
        """Parse ``"host:port"`` into a :class:`TCPConnectionConfig`."""
        host, _, port_str = s.rpartition(":")
        if not host or not port_str:
            raise ValueError(f"Invalid Modbus connection string {s!r}. Expected 'host:port', e.g. '192.168.1.10:502'.")
        return TCPConnectionConfig(host=host, port=int(port_str))


class SerialConnectionConfig(ModbusConnectionConfigBase):
    port: str  # e.g. "/dev/ttyUSB0" (Linux), "COM3" (Windows)
    baudrate: int = 9600
    parity: Literal["N", "E", "O"] = "N"  # none/even/odd
    stopbits: Literal[1, 2] = 1
    bytesize: Literal[5, 6, 7, 8] = 8

    def _format_target(self) -> str:
        return self.port + (f"@{self.unit_id}" if self.unit_id is not None else "")


class RTUConnectionConfig(SerialConnectionConfig):
    """Modbus RTU (serial) connection configuration."""

    transport: Literal["rtu"] = "rtu"


class ASCIIConnectionConfig(SerialConnectionConfig):
    transport: Literal["ascii"] = "ascii"


ModbusConnectionConfig = TCPConnectionConfig | RTUConnectionConfig | ASCIIConnectionConfig


# ── ModbusDriver ──────────────────────────────────────────────────────────────


def _modbus_op(fn):
    @functools.wraps(fn)
    def wrapper(self: ModbusDriver, *args, **kwargs):
        with self._lock:
            if self._client is None:
                raise RuntimeError("ModbusDriver is not open. Call open() first.")
            try:
                return fn(self, *args, **kwargs)
            except (OSError, ConnectionError, PymodbusConnectionException):
                if self._client is not None:
                    self._client.close()
                    self._client = None
                raise

    return wrapper


class ModbusDriver:
    """Synchronous Modbus transport driver.

    Wraps pymodbus with a focused public surface: ``open``, ``close``, and raw
    register read/write operations. Composed by concrete instrument drivers; not
    extended.

    Thread-safe by default. If ``thread_safe`` is False, consumer is responsible for
    ensuring application safety by calling this object in a thread-safe manner
    If ``thread_safe`` is True (default), the Modbus connection resource will be protected
    internally by a threading.RLock instance. This is recommended in any situation
    where application design does not ensure single-threaded access.
    """

    def __init__(self, connection: ModbusConnectionConfig, *, thread_safe: bool = True) -> None:
        self._connection = connection
        self._client: ModbusTcpClient | ModbusSerialClient | None = None
        self._lock: threading.RLock | contextlib.nullcontext = (
            threading.RLock() if thread_safe else contextlib.nullcontext()
        )

    @property
    def unit_id(self) -> int:
        """Unit/slave ID from the connection configuration."""
        return self._connection.unit_id

    def open(self) -> None:
        """Open the Modbus connection. Idempotent."""
        if self._client is not None:
            return
        connection = self._connection
        if isinstance(connection, TCPConnectionConfig):
            from pymodbus.client import ModbusTcpClient

            self._client = ModbusTcpClient(
                host=connection.host,
                port=connection.port,
                timeout=connection.timeout,
            )
        elif isinstance(connection, RTUConnectionConfig) or isinstance(connection, ASCIIConnectionConfig):
            from pymodbus.client import ModbusSerialClient
            from pymodbus.framer import FramerType

            self._client = ModbusSerialClient(
                framer=FramerType.RTU if isinstance(connection, RTUConnectionConfig) else FramerType.ASCII,
                port=connection.port,
                baudrate=connection.baudrate,
                parity=connection.parity,
                stopbits=connection.stopbits,
                bytesize=connection.bytesize,
                timeout=connection.timeout,
            )
        else:
            raise ValueError(f"Unknown connection type: {type(connection)}")

        if not self._client.connect():
            target = connection._format_target()
            self._client.close()
            self._client = None
            raise ConnectionError(f"Failed to connect to Modbus device at {target}")

    def close(self) -> None:
        """Close the Modbus connection. Idempotent."""
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def __del__(self) -> None:
        """Close on garbage collection."""
        self.close()

    # FC    Hex   Name                          Object type        Access
    # ───   ────  ────────────────────────────  ─────────────────  ──────
    # 01    0x01  Read Coils                    Coil (1-bit)       R
    # 02    0x02  Read Discrete Inputs          Discrete (1-bit)   R
    # 03    0x03  Read Holding Registers        Register (16-bit)  R
    # 04    0x04  Read Input Registers          Register (16-bit)  R
    # 05    0x05  Write Single Coil             Coil (1-bit)       W
    # 06    0x06  Write Single Register         Register (16-bit)  W
    # 15    0x0F  Write Multiple Coils          Coil (1-bit)       W
    # 16    0x10  Write Multiple Registers      Register (16-bit)  W
    # 22    0x16  Mask Write Register           Register (16-bit)  W
    # 23    0x17  Read/Write Multiple Registers Register (16-bit)  R/W

    # ── FC01: Read Coils ─────────────────────────────────────────────────────

    @_modbus_op
    def read_coils(self, address: int, count: int) -> list[bool]:
        """Read coils (FC01).

        ``Address`` is zero-indexed.
        Max ``count`` is 2000.
        """
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.read_coils(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(_format_error(f"reading coils at addr={address}", result))
        return list(result.bits[:count])

    # ── FC02: Read Discrete Inputs ────────────────────────────────────────────

    @_modbus_op
    def read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        """Read discrete inputs (FC02).

        ``Address`` is zero-indexed.
        Max ``count`` is 2000.
        """
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.read_discrete_inputs(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(_format_error(f"reading discrete inputs at addr={address}", result))
        return list(result.bits[:count])

    # ── FC03: Read Holding Registers ─────────────────────────────────────────

    @_modbus_op
    def read_holding_registers(self, address: int, count: int) -> list[int]:
        """Read holding registers (FC03).

        ``Address`` is zero-indexed.
        Max ``count`` is 125.
        """
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.read_holding_registers(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(_format_error(f"reading holding registers at addr={address}", result))
        return list(result.registers)

    # ── FC04: Read Input Registers ────────────────────────────────────────────

    @_modbus_op
    def read_input_registers(self, address: int, count: int) -> list[int]:
        """Read input registers (FC04).

        ``Address`` is zero-indexed.
        Max ``count`` is 125.
        """
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.read_input_registers(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(_format_error(f"reading input registers at addr={address}", result))
        return list(result.registers)

    # ── FC05: Write Single Coil ───────────────────────────────────────────────

    @_modbus_op
    def write_coil(self, address: int, value: bool) -> None:
        """Write a single coil (FC05)."""
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.write_coil(address, value, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(_format_error(f"writing coil at addr={address}", result))

    # ── FC06: Write Single Holding Register ──────────────────────────────────

    @_modbus_op
    def write_holding_register(self, address: int, value: int) -> None:
        """Write a single holding register (FC06)."""
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.write_register(address, value, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(_format_error(f"writing holding register at addr={address}", result))

    # ── FC15: Write Multiple Coils ────────────────────────────────────────────

    @_modbus_op
    def write_coils(self, address: int, values: list[bool]) -> None:
        """Write multiple coils (FC15).

        ``Address`` is zero-indexed.
        Max length of ``values`` is 1968.
        """
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.write_coils(address, values, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(_format_error(f"writing coils at addr={address}", result))

    # ── FC16: Write Multiple Holding Registers ────────────────────────────────

    @_modbus_op
    def write_holding_registers(self, address: int, values: list[int]) -> None:
        """Write multiple holding registers (FC16).

        ``Address`` is zero-indexed.
        Max length of ``values`` is 123.
        """
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.write_registers(address, values, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(_format_error(f"writing holding registers at addr={address}", result))

    # ── FC22: Mask Write Register ─────────────────────────────────────────────

    @_modbus_op
    def mask_write_register(self, address: int, and_mask: int, or_mask: int) -> None:
        """Mask write a single holding register (FC22).

        The device applies: new_value = (current_value AND and_mask) OR (or_mask AND NOT and_mask).
        """
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.mask_write_register(
            address=address, and_mask=and_mask, or_mask=or_mask, device_id=self.unit_id
        )
        if result.isError():
            raise RuntimeError(_format_error(f"mask writing register at addr={address}", result))

    # ── FC23: Read/Write Multiple Registers ───────────────────────────────────

    @_modbus_op
    def readwrite_holding_registers(
        self, read_address: int, read_count: int, write_address: int, write_values: list[int]
    ) -> list[int]:
        """Read and write holding registers in a single transaction (FC23).

        ``read_address`` and ``write_address`` are zero-indexed.
        Max ``read_count`` is 125.
        Max length of ``write_values`` is 121.
        """
        assert self._client is not None, "ModbusDriver is not open. Call open() first."
        result = self._client.readwrite_registers(
            read_address=read_address,
            read_count=read_count,
            write_address=write_address,
            values=write_values,
            device_id=self.unit_id,
        )
        if result.isError():
            raise RuntimeError(
                _format_error(f"read/write registers read_addr={read_address} write_addr={write_address}", result)
            )
        return list(result.registers)


# ── Helpers ───────────────────────────────────────────────────────────────────


_EXCEPTION_CODES: dict[int, str] = {
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


def _format_error(operation: str, result: object) -> str:
    code = getattr(result, "exception_code", 0)
    name = _EXCEPTION_CODES.get(code, "Unknown")
    if code:
        return f"Modbus error {operation}: {name} (0x{code:02X})"
    return f"Modbus error {operation}: {result}"
