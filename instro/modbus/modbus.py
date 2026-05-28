"""Modbus protocol interface (``ModbusDevice``)."""

from __future__ import annotations

import functools
import struct
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pymodbus.exceptions import ConnectionException as PymodbusConnectionException

from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher

from .types import (
    BOOL_DATA_TYPES,
    FLOAT_DATA_TYPES,
    INTEGER_DATA_TYPES,
    INTEGER_RANGES,
    ModbusConfig,
    RegisterDef,
    RTUConnection,
    TCPConnection,
)

if TYPE_CHECKING:
    from pymodbus.client import ModbusSerialClient, ModbusTcpClient


def _modbus_op(fn):
    """Acquire the lock, run the operation, and clear dead sockets on failure.

    The pymodbus *sync* client does NOT auto-reconnect between operations
    (``reconnect_delay`` is async-only). It does call ``connect()`` before
    every operation, which creates a new socket when ``self.socket is None``.
    So on a transport error we just close the dead socket and re-raise — the
    next call gets a fresh connection.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            if self._background_stop_event.is_set():
                raise RuntimeError("Instrument is shutting down.")
            if self._client is None:
                raise RuntimeError("Modbus client not connected. Call open() first.")
            assert self._client is not None
            try:
                return fn(self, *args, **kwargs)
            except (OSError, ConnectionError, PymodbusConnectionException):
                # Clear the dead socket so pymodbus's connect() creates a fresh one.
                self._client.close()
                raise

    return wrapper


class ModbusDevice(Instrument):
    """Config-driven Modbus client. Semantic access by register alias from a ``ModbusConfig``."""

    def __init__(
        self,
        config: ModbusConfig | dict | Path | str,
        connection: TCPConnection | RTUConnection | dict | None = None,
        name: str | None = None,
        publishers: list[Publisher] | None = None,
        autostart: bool = False,
        **kwargs,
    ):
        """Initialize a ModbusDevice.

        Args:
            config: A ``ModbusConfig``, a dict (validated via Pydantic), or a path to a JSON config.
            connection: Overrides ``config.connection``. Accepts a ``TCPConnection``,
                ``RTUConnection``, or a dict (with ``transport`` = ``"tcp"`` / ``"rtu"``).
                Required if the config has no ``connection`` section.
            name: Channel-name prefix; falls back to ``config.device.name``.
            publishers: Publishers that receive emitted Measurement/Command data.
            autostart: When True, open the connection and start background polling.
                Requires a ``timing`` section (with ``poll_interval``) — passing
                ``autostart=True`` without one is an error.
            **kwargs: Default tags applied to every emitted Measurement/Command.

        Raises:
            ValueError: No connection in args or config, or ``autostart=True`` with no ``timing`` section.
        """
        if isinstance(config, ModbusConfig):
            resolved_config = config
        elif isinstance(config, dict):
            resolved_config = ModbusConfig(**config)
        else:
            resolved_config = ModbusConfig.from_json(config)

        # Resolve connection: explicit parameter > config > error
        if connection is not None:
            if isinstance(connection, dict):
                transport = connection.get("transport")
                if transport == "tcp":
                    resolved_connection: TCPConnection | RTUConnection = TCPConnection(**connection)
                elif transport == "rtu":
                    resolved_connection = RTUConnection(**connection)
                else:
                    raise ValueError(f"Connection dict must include 'transport' as 'tcp' or 'rtu'; got {transport!r}.")
            else:
                resolved_connection = connection
        elif resolved_config.connection is not None:
            resolved_connection = resolved_config.connection
        else:
            raise ValueError(
                "No connection configuration provided. Either include a 'connection' section "
                "in the config or pass a 'connection' argument to ModbusDevice()."
            )

        instrument_name = name or resolved_config.device.name
        super().__init__(name=instrument_name, publishers=publishers, **kwargs)

        self._config = resolved_config
        self._connection = resolved_connection
        self._client: ModbusTcpClient | ModbusSerialClient | None = None
        self._lock = threading.RLock()

        self._define_background_daemon()

        if self._config.timing is not None:
            self.background_interval = self._config.timing.poll_interval

        if autostart:
            if self._config.timing is None:
                raise ValueError(
                    "autostart=True requires a 'timing' section in the config (with poll_interval). "
                    "Without polling configured, autostart has no effect — call open() manually instead."
                )
            self.open()
            self.start()

    def _define_background_daemon(self) -> None:
        """Register daemon polling: one call per ``read_group``; individual reads for ungrouped registers."""
        grouped_registers: set[str] = set()
        for group_id, regs in self._config._group_index.items():
            self.add_background_daemon_function(self._read_group, group_id)
            grouped_registers.update(r.name for r in regs)

        for reg in self._config.registers:
            if reg.poll and reg.name not in grouped_registers:
                self.add_background_daemon_function(self.read, reg.name)

    @property
    def unit_id(self) -> int:
        """Modbus unit/slave ID from the active connection config."""
        return self._connection.unit_id

    # ============ Connection Management ============

    def open(self) -> None:
        """Open the Modbus TCP/RTU connection."""
        with self._lock:
            self._background_stop_event.clear()
            if self._client is not None:
                return

            connection = self._connection
            if isinstance(connection, TCPConnection):
                from pymodbus.client import ModbusTcpClient

                self._client = ModbusTcpClient(
                    host=connection.host,
                    port=connection.port,
                    timeout=connection.timeout,
                )
            elif isinstance(connection, RTUConnection):
                from pymodbus.client import ModbusSerialClient

                self._client = ModbusSerialClient(
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
                if isinstance(connection, TCPConnection):
                    target = f"{connection.host}:{connection.port}"
                elif isinstance(connection, RTUConnection):
                    target = connection.port
                else:
                    target = str(connection)
                self._client.close()
                self._client = None
                raise ConnectionError(f"Failed to connect to Modbus device at {target}")

    def close(self) -> None:
        """Close the connection and stop the daemon."""
        self._background_stop_event.set()
        super().close()
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    # ============ Semantic Access (by alias) ============

    @publish_measurement
    def read(self, alias: str, **kwargs) -> Measurement:
        """Read the register named ``alias`` and return the scaled value."""
        reg = self._config.get_register(alias)
        raw_value = self._read_register_raw(reg)
        scaled_value = self._apply_scaling(raw_value, reg)
        channel_data = self._build_register_channels(reg, raw_value, scaled_value)
        return self._package_register_measurement(channel_data, **kwargs)

    @publish_measurement
    def _read_group(self, group_id: str, **kwargs) -> Measurement:
        """Read all registers in a group with a single Modbus transaction."""
        regs = self._config.get_group(group_id)
        first = regs[0]
        last = regs[-1]
        start_address = first.starting_address
        is_bit_type = first.register_type in ("coil", "discrete")

        if is_bit_type:
            total_count = (last.starting_address + 1) - start_address
        else:
            total_count = (last.starting_address + last.register_count) - start_address

        match first.register_type:
            case "holding":
                raw_regs = self._read_holding_registers(start_address, total_count)
            case "input":
                raw_regs = self._read_input_registers(start_address, total_count)
            case "coil":
                raw_bits = self._read_coils(start_address, total_count)
            case "discrete":
                raw_bits = self._read_discrete_inputs(start_address, total_count)
            case _:
                raise ValueError(f"Unknown register type: {first.register_type}")

        channel_data: dict[str, list[float | int]] = {}

        for reg in regs:
            offset = reg.starting_address - start_address
            if is_bit_type:
                raw_value: int | float = int(raw_bits[offset])
                scaled_value = raw_value
            else:
                reg_slice = raw_regs[offset : offset + reg.register_count]
                raw_value = self._decode_registers(
                    reg_slice, reg.data_type, reg.byte_swap, reg.word_swap, reg.long_swap
                )
                scaled_value = self._apply_scaling(raw_value, reg)

            channel_data.update(self._build_register_channels(reg, raw_value, scaled_value))

        return self._package_register_measurement(channel_data, **kwargs)

    def _build_register_channels(
        self, reg: "RegisterDef", raw_value: int | float, scaled_value: int | float
    ) -> dict[str, list[int | float]]:
        """Channel dict for ``reg``; emits one entry per bitmap bit when configured."""
        channel_data: dict[str, list[int | float]] = {f"{self.name}.{reg.name}": [scaled_value]}
        if reg.bitmap:
            int_value = int(raw_value)
            for bit in reg.bitmap:
                channel_data[f"{self.name}.{bit.name}"] = [(int_value >> bit.bit_index) & 1]
        return channel_data

    def _package_register_measurement(self, channel_data: dict[str, list[int | float]], **kwargs) -> Measurement:
        """Wrap a register-read ``channel_data`` dict in a multi-channel Measurement."""
        return Measurement(
            channel_data=channel_data,
            timestamps=[time.time_ns()],
            tags={**self.default_tags, **kwargs},
        )

    @publish_command
    def write(self, alias: str, value: float | int | bool | str, **kwargs) -> Command:
        """Write ``value`` to the register named ``alias``.

        ``value`` is in physical units when a ``scale`` is configured. For coils,
        pass ``True``/``False``. For registers with a ``write_value_map``, pass
        the string key to look up the mapped value.

        Raises:
            TypeError: Value type does not match the register's data type.
            KeyError: String value not found in ``write_value_map``.
            ValueError: Read-only register, value violates ``write_min``/``write_max``,
                or scaled raw is out of range for the data type.
        """
        reg = self._config.get_register(alias)

        # Resolve string values through the register's write_value_map
        if isinstance(value, str):
            if reg.write_value_map is None:
                raise KeyError(
                    f"Register '{alias}' has no write_value_map. "
                    f"Cannot write string '{value}' — pass a numeric value instead."
                )
            if value not in reg.write_value_map:
                raise KeyError(
                    f"'{value}' is not a valid value for register '{alias}'. "
                    f"Available values: {list(reg.write_value_map.keys())}"
                )
            value = reg.write_value_map[value]

        self._validate_write_value(value, reg, alias)

        if reg.scale is not None:
            raw_value: int | float = reg.scale.to_raw(value)
        else:
            raw_value = value

        raw_value = self._validate_raw_value_range(raw_value, reg, alias)

        self._write_register_raw(reg, raw_value)
        timestamp = time.time_ns()

        # Apply write delay
        if self._config.timing is not None and self._config.timing.write_delay_ms > 0:
            time.sleep(self._config.timing.write_delay_ms / 1000.0)

        # Build the Command inline rather than via `_package_command` so the raw value type
        # (int / bool / str) is preserved on the wire. Modbus has historically published the
        # untouched user-supplied value here, and downstream consumers may rely on
        # `int`/`bool`/`str` over the float coercion the base helper applies.
        return Command(
            channel_data={f"{self.name}.{alias}.cmd": value},
            timestamp=timestamp,
            tags={**self.default_tags, **(kwargs or {})},
        )

    # ============ Internal Helpers ============

    def _validate_write_value(self, value: float | int, reg: RegisterDef, alias: str) -> None:
        """Reject value types that don't match the register's data type (bool vs int, fractional int, range)."""
        if reg.register_type in ("input", "discrete"):
            raise ValueError(
                f"Register '{alias}' is read-only (register_type='{reg.register_type}'). "
                f"Cannot write to input registers or discrete inputs."
            )

        if reg.write_min is not None and value < reg.write_min:
            raise ValueError(f"Register '{alias}' value {value} is below write_min ({reg.write_min}).")
        if reg.write_max is not None and value > reg.write_max:
            raise ValueError(f"Register '{alias}' value {value} is above write_max ({reg.write_max}).")

        data_type = reg.data_type
        is_bool_register = data_type in BOOL_DATA_TYPES or reg.register_type == "coil"
        is_int_register = data_type in INTEGER_DATA_TYPES
        is_float_register = data_type in FLOAT_DATA_TYPES

        if is_bool_register:
            if isinstance(value, bool):
                return
            if isinstance(value, int) and value in (0, 1):
                return
            raise TypeError(
                f"Register '{alias}' is a bool/coil type but got {type(value).__name__} value {value!r}. "
                f"Use True/False or 0/1."
            )

        if is_int_register:
            if isinstance(value, bool):
                raise TypeError(
                    f"Register '{alias}' is an integer type ({data_type}) but got bool. Use an integer value."
                )
            if reg.scale is None and isinstance(value, float) and value != int(value):
                raise TypeError(
                    f"Register '{alias}' is an integer type ({data_type}) but got float {value}. "
                    f"Value would be truncated to {int(value)}. Use an integer or round explicitly."
                )
            if reg.scale is None and data_type in INTEGER_RANGES:
                int_val = int(value)
                min_val, max_val = INTEGER_RANGES[data_type]
                if int_val < min_val or int_val > max_val:
                    raise ValueError(
                        f"Register '{alias}' value {int_val} is out of range for {data_type} [{min_val}, {max_val}]."
                    )
            return

        if is_float_register:
            if isinstance(value, bool):
                raise TypeError(f"Register '{alias}' is a float type ({data_type}) but got bool. Use a numeric value.")
            return

    def _validate_raw_value_range(
        self, raw_value: int | float | bool, reg: RegisterDef, alias: str
    ) -> int | float | bool:
        """Post-scaling range check for integer registers; rejects non-integer scaled raws."""
        data_type = reg.data_type

        if data_type not in INTEGER_DATA_TYPES:
            return raw_value

        if reg.scale is not None and isinstance(raw_value, float):
            rounded = round(raw_value)
            if abs(raw_value - rounded) > 1e-6:
                raise TypeError(
                    f"Register '{alias}' scaled raw value {raw_value} has a fractional part, "
                    f"but {data_type} requires an integer. Check your scaling configuration or input value."
                )
            raw_value = rounded

        if data_type not in INTEGER_RANGES:
            return raw_value

        min_val, max_val = INTEGER_RANGES[data_type]
        int_raw = int(raw_value)

        if int_raw < min_val or int_raw > max_val:
            if reg.scale is not None:
                raise ValueError(
                    f"Register '{alias}': raw value {int_raw} after scaling is out of range "
                    f"for {data_type} [{min_val}, {max_val}]. "
                    f"The physical value resulted in a raw value that overflows the register type."
                )
            else:
                raise ValueError(
                    f"Register '{alias}' value {int_raw} is out of range for {data_type} [{min_val}, {max_val}]."
                )

        return raw_value

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

    @_modbus_op
    def _read_holding_registers(self, address: int, count: int) -> list[int]:
        """Read holding registers by address (FC03)."""
        assert self._client is not None
        result = self._client.read_holding_registers(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"reading holding registers at addr={address}", result))
        return list(result.registers)

    @_modbus_op
    def _read_input_registers(self, address: int, count: int) -> list[int]:
        """Read input registers by address (FC04)."""
        assert self._client is not None
        result = self._client.read_input_registers(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"reading input registers at addr={address}", result))
        return list(result.registers)

    @_modbus_op
    def _write_holding_register(self, address: int, value: int) -> None:
        """Write a single holding register by address (FC06)."""
        assert self._client is not None
        result = self._client.write_register(address, value, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"writing holding register at addr={address}", result))

    @_modbus_op
    def _write_holding_registers(self, address: int, values: list[int]) -> None:
        """Write multiple holding registers by address (FC16)."""
        assert self._client is not None
        result = self._client.write_registers(address, values, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"writing holding registers at addr={address}", result))

    @_modbus_op
    def _read_coils(self, address: int, count: int) -> list[bool]:
        """Read coils by address (FC01)."""
        assert self._client is not None
        result = self._client.read_coils(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"reading coils at addr={address}", result))
        return list(result.bits[:count])

    @_modbus_op
    def _write_coil(self, address: int, value: bool) -> None:
        """Write a single coil by address (FC05)."""
        assert self._client is not None
        result = self._client.write_coil(address, value, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"writing coil at addr={address}", result))

    @_modbus_op
    def _write_coils(self, address: int, values: list[bool]) -> None:
        """Write multiple coils by address (FC15)."""
        assert self._client is not None
        result = self._client.write_coils(address, values, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"writing coils at addr={address}", result))

    @_modbus_op
    def _read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        """Read discrete inputs by address (FC02)."""
        assert self._client is not None
        result = self._client.read_discrete_inputs(address, count=count, device_id=self.unit_id)
        if result.isError():
            raise RuntimeError(self._format_modbus_error(f"reading discrete inputs at addr={address}", result))
        return list(result.bits[:count])

    def _read_register_raw(self, reg: RegisterDef) -> int | float:
        """Dispatch by ``register_type`` (holding/input/coil/discrete) and decode by ``data_type``."""
        match reg.register_type:
            case "holding":
                raw_regs = self._read_holding_registers(reg.starting_address, reg.register_count)
            case "input":
                raw_regs = self._read_input_registers(reg.starting_address, reg.register_count)
            case "coil":
                bits = self._read_coils(reg.starting_address, 1)
                return int(bits[0])
            case "discrete":
                bits = self._read_discrete_inputs(reg.starting_address, 1)
                return int(bits[0])
            case _:
                raise ValueError(f"Unknown register type: {reg.register_type}")

        return self._decode_registers(raw_regs, reg.data_type, reg.byte_swap, reg.word_swap, reg.long_swap)

    def _write_register_raw(self, reg: RegisterDef, value: int | float | bool) -> None:
        """Encode ``value`` per ``data_type`` and dispatch to the matching write function code."""
        match reg.register_type:
            case "holding":
                encoded = self._encode_value(value, reg.data_type, reg.byte_swap, reg.word_swap, reg.long_swap)
                if len(encoded) == 1:
                    self._write_holding_register(reg.starting_address, encoded[0])
                else:
                    self._write_holding_registers(reg.starting_address, encoded)
            case "coil":
                self._write_coil(reg.starting_address, bool(value))
            case "input" | "discrete":
                raise ValueError(f"Cannot write to read-only register type: {reg.register_type}")
            case _:
                raise ValueError(f"Unknown register type: {reg.register_type}")

    def _decode_registers(
        self, registers: list[int], data_type: str, byte_swap: bool, word_swap: bool, long_swap: bool
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

    def _encode_value(
        self, value: int | float | bool, data_type: str, byte_swap: bool, word_swap: bool, long_swap: bool
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

    def _apply_scaling(self, raw_value: int | float, reg: RegisterDef) -> int | float:
        """Apply ``reg.scale`` to ``raw_value`` if scaling is configured; otherwise pass through."""
        if reg.scale is not None:
            return reg.scale.to_physical(raw_value)
        return raw_value
