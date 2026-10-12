"""Modbus protocol interface (``ModbusDevice``)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import cast

from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.transports.modbus import ModbusTransport, decode_registers

from .types import (
    _CONNECTION_ADAPTER,
    BOOL_DATA_TYPES,
    FLOAT_DATA_TYPES,
    INTEGER_DATA_TYPES,
    INTEGER_RANGES,
    ModbusConfig,
    RegisterDef,
    _ConnectionConfig,
)


class ModbusDevice(Instrument):
    """Config-driven Modbus client. Semantic access by register alias from a ``ModbusConfig``."""

    def __init__(
        self,
        config: ModbusConfig | dict | Path | str,
        connection: ModbusTransport | dict | None = None,
        name: str | None = None,
        publishers: list[Publisher] | None = None,
        autostart: bool = False,
        unit_id: int | None = None,
        **kwargs,
    ):
        """Initialize a ModbusDevice.

        Args:
            config: A ``ModbusConfig``, a dict (validated via Pydantic), or a path to a JSON config.
            connection: Overrides ``config.connection``. Accepts a ``ModbusTransport`` (pass the
                same instance to several devices to share one line; ``unit_id`` is then
                required), or a dict (with ``transport`` = ``"tcp"`` / ``"rtu"``), which builds a
                private transport. Required if the config has no ``connection`` section.
            name: Channel-name prefix; falls back to ``config.device.name``.
            publishers: Publishers that receive emitted Measurement/Command data.
            autostart: When True, open the connection and start background polling.
                Requires a ``timing`` section (with ``poll_interval``) — passing
                ``autostart=True`` without one is an error.
            unit_id: Modbus unit/slave address (0-255; 0-247 on serial). May also come
                from the connection block's ``unit_id`` field; values given in both places
                must agree. Required alongside a shared transport; defaults to 1 when this
                device builds its own private transport.
            **kwargs: Default tags applied to every emitted Measurement/Command.

        Raises:
            ValueError: No connection in args or config, an address that is missing, conflicting,
                or out of range, or ``autostart=True`` with no ``timing`` section.
        """
        if isinstance(config, ModbusConfig):
            resolved_config = config
        elif isinstance(config, dict):
            resolved_config = ModbusConfig(**config)
        else:
            resolved_config = ModbusConfig.from_json(config)

        transport, unit_id = self._resolve_connection(connection, resolved_config, unit_id)

        instrument_name = name or resolved_config.device.name
        super().__init__(name=instrument_name, publishers=publishers, **kwargs)

        self._config = resolved_config
        self._transport = transport
        self._unit_id = unit_id
        self._opened = False

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

    @staticmethod
    def _resolve_connection(
        connection: ModbusTransport | dict | None,
        config: ModbusConfig,
        unit_id: int | None,
    ) -> tuple[ModbusTransport, int]:
        """Resolve ``connection`` into a transport and the unit address this device talks at."""
        connection_unit_id: int | None = None
        match connection:
            case ModbusTransport():
                transport = connection
            case dict():
                # Pydantic owns the tcp/rtu dispatch and the bad-`transport` error; there is no
                # hand-written branch on the "transport" key.
                block: _ConnectionConfig = _CONNECTION_ADAPTER.validate_python(connection)
                transport = block.build()
                connection_unit_id = block.unit_id
            case None if config.connection is not None:
                transport = config.connection.build()
                connection_unit_id = config.connection.unit_id
            case _:
                raise ValueError(
                    "No connection configuration provided. Either include a 'connection' section "
                    "in the config or pass a 'connection' argument to ModbusDevice()."
                )

        if unit_id is not None and connection_unit_id is not None and unit_id != connection_unit_id:
            raise ValueError(
                f"unit_id={unit_id} (constructor) conflicts with connection.unit_id={connection_unit_id}. "
                "Specify only one."
            )
        if unit_id is None:
            unit_id = connection_unit_id
        if unit_id is None:
            if isinstance(connection, ModbusTransport):
                raise ValueError(
                    "A shared ModbusTransport needs an explicit unit address: pass unit_id "
                    "to the constructor or set it in the config."
                )
            # 0 is the broadcast address and must not be rewritten to 1; defaulting is safe
            # only here, on a transport nobody else can reach.
            unit_id = 1
        return transport, transport.check_unit_id(unit_id)

    @property
    def unit_id(self) -> int:
        """Modbus unit/slave address this device is bound to. Read-only: set once at construction, via the constructor kwarg or the config."""
        return self._unit_id

    def _require_ready_locked(self) -> None:
        """Reject I/O during shutdown, before this device opened, or before the transport is open."""
        if self._background_stop_event.is_set():
            raise RuntimeError("Instrument is shutting down.")
        if not self._opened or not self._transport.is_open:
            raise RuntimeError("Modbus client not connected. Call open() first.")

    # ============ Connection Management ============

    def open(self) -> None:
        """Open the Modbus TCP/RTU connection, registering this device as an owner of the transport."""
        self._background_stop_event.clear()
        self._transport.open(self)
        self._opened = True

    def close(self) -> None:
        """Release this device's hold on the transport and stop the daemon; the line closes when the last device leaves."""
        self._background_stop_event.set()
        super().close()
        if self._opened:
            self._transport.close(self)
            self._opened = False

    # ============ Semantic Access (by alias) ============

    @publish_measurement
    def read(self, alias: str, **kwargs) -> Measurement:
        """Read the register named ``alias`` and return the scaled value."""
        reg = self._config.get_register(alias)
        with self._transport.lock():
            self._require_ready_locked()
            raw_value = self._transport.read_typed(
                reg.register_type,
                reg.starting_address,
                reg.data_type,
                unit_id=self._unit_id,
                byte_swap=reg.byte_swap,
                word_swap=reg.word_swap,
                long_swap=reg.long_swap,
            )
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

        with self._transport.lock():
            self._require_ready_locked()
            match first.register_type:
                case "holding":
                    raw_regs = self._transport.read_holding_registers(start_address, total_count, unit_id=self._unit_id)
                case "input":
                    raw_regs = self._transport.read_input_registers(start_address, total_count, unit_id=self._unit_id)
                case "coil":
                    raw_bits = self._transport.read_coils(start_address, total_count, unit_id=self._unit_id)
                case "discrete":
                    raw_bits = self._transport.read_discrete_inputs(start_address, total_count, unit_id=self._unit_id)
                case _:
                    raise ValueError(f"Unknown register type: {first.register_type}")

        channel_data: dict[str, list[float] | list[str]] = {}

        for reg in regs:
            offset = reg.starting_address - start_address
            if is_bit_type:
                raw_value: int | float | bool = bool(raw_bits[offset])
                scaled_value = raw_value
            else:
                reg_slice = raw_regs[offset : offset + reg.register_count]
                raw_value = decode_registers(reg_slice, reg.data_type, reg.byte_swap, reg.word_swap, reg.long_swap)
                scaled_value = self._apply_scaling(raw_value, reg)

            channel_data.update(self._build_register_channels(reg, raw_value, scaled_value))

        return self._package_register_measurement(channel_data, **kwargs)

    def _build_register_channels(
        self, reg: "RegisterDef", raw_value: int | float, scaled_value: int | float
    ) -> dict[str, list[float] | list[str]]:
        """Channel dict for ``reg``; emits one entry per bitmap bit when configured."""
        channel_data: dict[str, list[float] | list[str]] = {f"{self.name}.{reg.name}": [scaled_value]}
        if reg.bitmap:
            int_value = int(raw_value)
            for bit in reg.bitmap:
                # A type-only assertion, not a cast to float: the published bit stays a plain
                # int (0 or 1), matching modbus.mdx's documented `0`/`1` values. A subscript
                # assignment's list literal doesn't get the same contextual type inference a
                # dict-literal construction does, so mypy needs this spelled out explicitly.
                channel_data[f"{self.name}.{bit.name}"] = cast(list[float], [(int_value >> bit.bit_index) & 1])
        return channel_data

    def _package_register_measurement(self, channel_data: dict[str, list[float] | list[str]], **kwargs) -> Measurement:
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

        # Coils are single-bit: the transport requires a real bool. _validate_write_value has
        # already constrained the value to bool or 0/1, so coercing here is safe.
        wire_value = bool(raw_value) if reg.register_type == "coil" else raw_value

        with self._transport.lock():
            self._require_ready_locked()
            self._transport.write_typed(
                reg.register_type,
                reg.starting_address,
                wire_value,
                reg.data_type,
                unit_id=self._unit_id,
                byte_swap=reg.byte_swap,
                word_swap=reg.word_swap,
                long_swap=reg.long_swap,
            )
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

    def _apply_scaling(self, raw_value: int | float, reg: RegisterDef) -> int | float:
        """Apply ``reg.scale`` to ``raw_value`` if scaling is configured; otherwise pass through."""
        if reg.scale is not None:
            return reg.scale.to_physical(raw_value)
        return raw_value
