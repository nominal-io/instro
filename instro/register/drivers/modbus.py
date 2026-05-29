"""Modbus implementation for register-access driver.

Implements `ModbusRegisterDriver` for modbus using the ModbusDriver wrapper around pymodbus.

Includes configuration definitions from the protocol layer as well as register/data type layers

Public API:
    ModbusRegisterDriver

"""

from __future__ import annotations

import contextlib
import struct
import threading
import time
from functools import cached_property
from pathlib import Path
from typing import Literal, Mapping, Sequence, cast

from pydantic import BaseModel, Field, model_validator

from instro.register import RegisterBase, RegisterDriverBase, register_value_type
from instro.utils.protocol.modbus import ModbusConnectionConfig, ModbusDriver
from instro.utils.types import DeviceInfo, LinearScale, ScaleType

# ============ Bitmap Definition ============


class BitDef(BaseModel):
    """Definition of a single bit to extract from a uint16 register."""

    name: str = Field(description="Channel name for this bit")
    bit_index: int = Field(ge=0, le=15, description="0-based bit position from LSB")


# ============ Protocol Constants ============

# Max registers per FC03/FC04 read (250 bytes / 2 bytes per register)
MAX_REGISTERS_PER_READ = 125

# Max coils/discrete inputs per FC01/FC02 read (250 bytes * 8 bits per byte)
MAX_COILS_PER_READ = 2000

# ============ Data Type Constants ============

# Single source of truth: every integer data type maps to its valid raw-value range.
# INTEGER_DATA_TYPES is derived from this so the two can never drift out of sync.
INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "uint16": (0, 65535),
    "int16": (-32768, 32767),
    "uint32": (0, 4294967295),
    "int32": (-2147483648, 2147483647),
    "uint64": (0, 18446744073709551615),
    "int64": (-9223372036854775808, 9223372036854775807),
}
INTEGER_DATA_TYPES = tuple(INTEGER_RANGES)
FLOAT_DATA_TYPES = ("float32", "float64")
BOOL_DATA_TYPES = ("bool",)
ALL_DATA_TYPES = INTEGER_DATA_TYPES + FLOAT_DATA_TYPES + BOOL_DATA_TYPES

# Type alias for use in Literal annotations
DataType = Literal[
    "uint16",
    "int16",
    "uint32",
    "int32",
    "uint64",
    "int64",
    "float32",
    "float64",
    "bool",
]


# ============ Timing Config ============


class TimingConfig(BaseModel):
    """Timing configuration for Modbus polling and write delays."""

    poll_interval: float = Field(ge=0.01, le=10.0, description="Polling interval in seconds")
    write_delay_ms: int = Field(default=0, ge=0, description="Delay in milliseconds applied after every write")


# ============ Connection Configs ============

# The discriminated union of all supported connection types, defined in
# instro.utils.protocol.modbus and aliased here for use in ModbusConfig.
ConnectionType = ModbusConnectionConfig


# ============ Register Definition ============


class ModbusRegisterDef(RegisterBase):
    """Definition of a Modbus register.

    Note:
        - Swap options control byte ordering for multi-byte values:
          - `byte_swap`: swap bytes within each 16-bit word
          - `word_swap`: swap 16-bit words (for 32-bit and 64-bit types)
          - `long_swap`: swap 32-bit longs (for 64-bit types only)
        - All swap options default to False (big-endian / network byte order).
        - `scale` is not allowed for coils and discrete inputs.
    """

    starting_address: int = Field(ge=0, le=65535)
    register_type: Literal["holding", "input", "coil", "discrete"] = "holding"
    data_type: DataType = "uint16"
    byte_swap: bool = False
    word_swap: bool = False
    long_swap: bool = False
    bitmap: list[BitDef] | None = None
    write_min: float | int | None = None
    write_max: float | int | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_bool_in_value_map_for_non_bool_registers(cls, data: object) -> object:
        """Reject bool ``write_value_map`` entries on integer registers before Pydantic coerces them.

        Pydantic v2 silently coerces True/False → 1/0 in ``dict[str, int | float]``,
        which would let ``{"enable": True}`` on a uint16 register validate and hide
        the author's intent. Bool is still allowed on bool/coil registers, mirroring
        runtime ``_validate_write_value``.
        """
        if not isinstance(data, dict):
            return data
        wvm = data.get("write_value_map")
        if not isinstance(wvm, dict):
            return data
        data_type = data.get("data_type", "uint16")
        register_type = data.get("register_type", "holding")
        if data_type == "bool" or register_type == "coil":
            return data
        for label, raw in wvm.items():
            if isinstance(raw, bool):
                raise ValueError(
                    f"write_value_map entry '{label}' ({raw}) is a bool on a "
                    f"{register_type}/{data_type} register. Use an integer (0 or 1) "
                    f"to make the intent explicit."
                )
        return data

    @model_validator(mode="after")
    def _validate_register_type_constraints(self) -> ModbusRegisterDef:
        """Cross-field validation: scale, swap flags, address span, write limits, bitmap, write_value_map."""
        # scale is not allowed for coils and discrete inputs (single-bit values)
        if self.register_type in ("coil", "discrete") and self.scale is not None:
            raise ValueError(
                f"scale is not allowed for {self.register_type} registers "
                "(coils and discrete inputs are single-bit boolean values)"
            )

        # word_swap should not be used for 16-bit types or bool (only applies to larger types)
        if self.word_swap and self.data_type in ("uint16", "int16", "bool"):
            raise ValueError(
                f"word_swap is not applicable for {self.data_type} (only applies to 32-bit and 64-bit types)"
            )

        # long_swap only applies to 64-bit types
        if self.long_swap and self.data_type not in ("uint64", "int64", "float64"):
            raise ValueError(f"long_swap is not applicable for {self.data_type} (only applies to 64-bit types)")

        # Multi-register data types must fit entirely within the Modbus address space (0-65535).
        end_address = self.starting_address + self.register_count - 1
        if end_address > 65535:
            raise ValueError(
                f"register spans addresses {self.starting_address}-{end_address}, which exceeds "
                f"the Modbus address space [0, 65535]. Reduce starting_address or use a smaller data_type."
            )

        # write_min/write_max/write_value_map only allowed on holding registers
        write_fields = []
        if self.write_min is not None or self.write_max is not None:
            write_fields.append("write_min/write_max")
        if self.write_value_map is not None:
            write_fields.append("write_value_map")
        if write_fields and self.register_type != "holding":
            raise ValueError(
                f"{', '.join(write_fields)} only allowed on holding registers, got register_type='{self.register_type}'"
            )
        if self.write_min is not None and self.write_max is not None and self.write_min > self.write_max:
            raise ValueError(f"write_min ({self.write_min}) must be less than or equal to write_max ({self.write_max})")

        # write_min/write_max must be within the data type's range (scaling-aware)
        if self.data_type in INTEGER_RANGES:
            dt_min, dt_max = INTEGER_RANGES[self.data_type]
            for field_name, field_val in [("write_min", self.write_min), ("write_max", self.write_max)]:
                if field_val is not None:
                    raw_val = self.scale.to_raw(field_val) if self.scale is not None else field_val
                    if raw_val < dt_min or raw_val > dt_max:
                        if self.scale is not None:
                            raise ValueError(
                                f"{field_name} ({field_val}) converts to raw value {raw_val}, "
                                f"which is out of range for {self.data_type} [{dt_min}, {dt_max}]"
                            )
                        raise ValueError(
                            f"{field_name} ({field_val}) is out of range for {self.data_type} [{dt_min}, {dt_max}]"
                        )

        # write_value_map: values must be unique, within limits, and type-compatible.
        # These checks mirror the runtime validators in ModbusDevice so that
        # anything accepted at config time also works at runtime, and vice versa.
        if self.write_value_map is not None:
            is_int_type = self.data_type in INTEGER_DATA_TYPES
            is_bool_type = self.data_type in BOOL_DATA_TYPES or self.register_type == "coil"
            seen_values: dict[int | float, str] = {}
            for label, raw in self.write_value_map.items():
                if raw in seen_values:
                    raise ValueError(
                        f"Duplicate value {raw} in write_value_map: '{seen_values[raw]}' and '{label}' both map to {raw}"
                    )
                if is_bool_type:
                    # Runtime accepts only bool or int in {0, 1}; floats (even 1.0) are rejected.
                    if not (
                        isinstance(raw, bool) or (isinstance(raw, int) and not isinstance(raw, bool) and raw in (0, 1))
                    ):
                        raise ValueError(
                            f"write_value_map entry '{label}' ({raw}) must be True/False or 0/1 "
                            f"for {self.register_type} register '{self.name}' (data_type='{self.data_type}')."
                        )
                elif is_int_type:
                    # Note: Pydantic v2 coerces bool -> int in dict[str, int | float] fields
                    # before this validator runs, so bool values never appear here. The runtime
                    # bool-rejection in _validate_write_value is reached via user code that
                    # passes bool directly to device.write(), not via map lookups.
                    #
                    # Without a scale, fractional floats can never become a valid raw integer.
                    # With a scale, physical-space fractions are fine as long as the raw result
                    # is (close to) an integer; that's checked below.
                    if self.scale is None and isinstance(raw, float) and raw != int(raw):
                        raise ValueError(
                            f"write_value_map entry '{label}' ({raw}) is a non-integer float, "
                            f"but register data_type is '{self.data_type}'"
                        )
                    # is_int_type implies data_type is in INTEGER_RANGES (they share keys).
                    dt_min, dt_max = INTEGER_RANGES[self.data_type]
                    raw_check = self.scale.to_raw(raw) if self.scale is not None else raw
                    # Match _validate_raw_value_range: scaled raw must be close to an integer.
                    if self.scale is not None and isinstance(raw_check, float):
                        if abs(raw_check - round(raw_check)) > 1e-6:
                            raise ValueError(
                                f"write_value_map entry '{label}' ({raw}) converts to raw value "
                                f"{raw_check}, which is not an integer. {self.data_type} requires "
                                f"integer raw values — check the scaling configuration."
                            )
                    if round(raw_check) < dt_min or round(raw_check) > dt_max:
                        if self.scale is not None:
                            raise ValueError(
                                f"write_value_map entry '{label}' ({raw}) converts to raw value {round(raw_check)}, "
                                f"which is out of range for {self.data_type} [{dt_min}, {dt_max}]"
                            )
                        raise ValueError(
                            f"write_value_map entry '{label}' ({raw}) is out of range "
                            f"for {self.data_type} [{dt_min}, {dt_max}]"
                        )
                if self.write_min is not None and raw < self.write_min:
                    raise ValueError(f"write_value_map entry '{label}' ({raw}) is below write_min ({self.write_min})")
                if self.write_max is not None and raw > self.write_max:
                    raise ValueError(f"write_value_map entry '{label}' ({raw}) is above write_max ({self.write_max})")
                seen_values[raw] = label

        # bitmap only applies to uint16 holding or input registers
        if self.bitmap is not None:
            if self.data_type != "uint16":
                raise ValueError(f"bitmap is only supported for uint16 data type, got '{self.data_type}'")
            if self.register_type not in ("holding", "input"):
                raise ValueError(f"bitmap is only supported for holding or input registers, got '{self.register_type}'")
            bit_indices = [b.bit_index for b in self.bitmap]
            if len(bit_indices) != len(set(bit_indices)):
                seen: set[int] = set()
                dupes: list[int] = []
                for i in bit_indices:
                    if i in seen:
                        dupes.append(i)
                    seen.add(i)
                raise ValueError(f"Duplicate bit_index values in bitmap: {sorted(set(dupes))}")

        return self

    @property
    def register_count(self) -> int:
        """Number of 16-bit registers this data type spans (uint16→1, uint32→2, uint64→4)."""
        match self.data_type:
            case "uint16" | "int16":
                return 1
            case "uint32" | "int32" | "float32":
                return 2
            case "uint64" | "int64" | "float64":
                return 4
            case "bool":
                return 1
            case _:
                return 1

    def _validate_write_value(self, value: float | int) -> None:
        """Validate that the value type is compatible with the register's data type."""
        if self.register_type in ("input", "discrete"):
            raise ValueError(
                f"Register '{self.name}' is read-only (register_type='{self.register_type}'). "
                f"Cannot write to input registers or discrete inputs."
            )

        if self.write_min is not None and value < self.write_min:
            raise ValueError(f"Register '{self.name}' value {value} is below write_min ({self.write_min}).")
        if self.write_max is not None and value > self.write_max:
            raise ValueError(f"Register '{self.name}' value {value} is above write_max ({self.write_max}).")

        data_type = self.data_type
        is_bool_register = data_type in BOOL_DATA_TYPES or self.register_type == "coil"
        is_int_register = data_type in INTEGER_DATA_TYPES
        is_float_register = data_type in FLOAT_DATA_TYPES

        if is_bool_register:
            if isinstance(value, bool):
                return
            if isinstance(value, int) and value in (0, 1):
                return
            raise TypeError(
                f"Register '{self.name}' is a bool/coil type but got {type(value).__name__} value {value!r}. "
                f"Use True/False or 0/1."
            )

        if is_int_register:
            if isinstance(value, bool):
                raise TypeError(
                    f"Register '{self.name}' is an integer type ({data_type}) but got bool. Use an integer value."
                )
            if self.scale is None and isinstance(value, float) and value != int(value):
                raise TypeError(
                    f"Register '{self.name}' is an integer type ({data_type}) but got float {value}. "
                    f"Value would be truncated to {int(value)}. Use an integer or round explicitly."
                )
            if self.scale is None and data_type in INTEGER_RANGES:
                int_val = int(value)
                min_val, max_val = INTEGER_RANGES[data_type]
                if int_val < min_val or int_val > max_val:
                    raise ValueError(
                        f"Register '{self.name}' value {int_val} is out of range for {data_type} [{min_val}, {max_val}]."
                    )
            return

        if is_float_register:
            if isinstance(value, bool):
                raise TypeError(
                    f"Register '{self.name}' is a float type ({data_type}) but got bool. Use a numeric value."
                )
            return

    def _validate_raw_value_range(self, raw_value: int | float | bool) -> int | float | bool:
        """Validate that the raw value (after scaling) is in range for the register's data type."""
        data_type = self.data_type

        if data_type not in INTEGER_DATA_TYPES:
            return raw_value

        if self.scale is not None and isinstance(raw_value, float):
            rounded = round(raw_value)
            if abs(raw_value - rounded) > 1e-6:
                raise TypeError(
                    f"Register '{self.name}' scaled raw value {raw_value} has a fractional part, "
                    f"but {data_type} requires an integer. Check your scaling configuration or input value."
                )
            raw_value = rounded

        if data_type not in INTEGER_RANGES:
            return raw_value

        min_val, max_val = INTEGER_RANGES[data_type]
        int_raw = int(raw_value)

        if int_raw < min_val or int_raw > max_val:
            if self.scale is not None:
                raise ValueError(
                    f"Register '{self.name}': raw value {int_raw} after scaling is out of range "
                    f"for {data_type} [{min_val}, {max_val}]. "
                    f"The physical value resulted in a raw value that overflows the register type."
                )
            else:
                raise ValueError(
                    f"Register '{self.name}' value {int_raw} is out of range for {data_type} [{min_val}, {max_val}]."
                )

        return raw_value

    def decode_register_values(self, register_values: list[int]):
        """Decode raw 16-bit registers into a typed value."""
        raw_bytes = b"".join(reg.to_bytes(2, "big") for reg in register_values)

        if self.byte_swap:
            raw_bytes = b"".join(raw_bytes[i : i + 2][::-1] for i in range(0, len(raw_bytes), 2))

        if self.word_swap and len(raw_bytes) >= 4:
            swapped = bytearray()
            for i in range(0, len(raw_bytes), 4):
                chunk = raw_bytes[i : i + 4]
                if len(chunk) == 4:
                    swapped.extend(chunk[2:4] + chunk[0:2])
                else:
                    swapped.extend(chunk)
            raw_bytes = bytes(swapped)

        if self.long_swap and len(raw_bytes) == 8:
            raw_bytes = raw_bytes[4:8] + raw_bytes[0:4]

        match self.data_type:
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
                return int(register_values[0] != 0)
            case _:
                raise ValueError(f"Unknown data type: {self.data_type}")

    def encode_value_to_registers(self, value: int | float | bool) -> list[int]:
        """Encode a typed value into 16-bit registers."""
        match self.data_type:
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
                raise ValueError(f"Unknown data type: {self.data_type}")

        if self.long_swap and len(raw_bytes) == 8:
            raw_bytes = raw_bytes[4:8] + raw_bytes[0:4]

        if self.word_swap and len(raw_bytes) >= 4:
            swapped = bytearray()
            for i in range(0, len(raw_bytes), 4):
                chunk = raw_bytes[i : i + 4]
                if len(chunk) == 4:
                    swapped.extend(chunk[2:4] + chunk[0:2])
                else:
                    swapped.extend(chunk)
            raw_bytes = bytes(swapped)

        if self.byte_swap:
            raw_bytes = b"".join(raw_bytes[i : i + 2][::-1] for i in range(0, len(raw_bytes), 2))

        return [int.from_bytes(raw_bytes[i : i + 2], "big") for i in range(0, len(raw_bytes), 2)]


# ============ Top-Level Config ============


class ModbusConfig(BaseModel):
    """Complete Modbus device configuration. Load from JSON via ``ModbusConfig.from_json(path)``."""

    version: int = 1
    protocol: str = "modbus"
    device: DeviceInfo
    timing: TimingConfig | None = None
    connection: ConnectionType = Field(discriminator="transport")
    registers: list[ModbusRegisterDef] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        """Cross-register validation: uniqueness, overlap, groups."""
        self._validate_protocol()
        self._validate_unique_register_names()
        self._validate_unique_bitmap_names()
        self._validate_no_register_overlap()
        self._validate_group_register_types()
        self._validate_group_span()
        self._validate_no_non_group_register_in_group_span()

    # `registers` is effectively immutable after __init__ (no callers mutate it), so both
    # indices are safe to cache. These are hit on every background poll via _read_group,
    # so rebuilding on every access was real wasted work.
    # Type hints reflect the immutability (Mapping and Sequence are not mutable)
    @cached_property
    def _register_index(self) -> Mapping[str, ModbusRegisterDef]:
        return {reg.name: reg for reg in self.registers}

    @cached_property
    def _group_index(self) -> Mapping[str, Sequence[ModbusRegisterDef]]:
        return self._build_group_index()

    @cached_property
    def _writeable_registers(self) -> Sequence[ModbusRegisterDef]:
        return [reg for reg in self.registers if reg.register_type in ("holding", "coil")]

    @cached_property
    def _writeable_groups(self) -> list[str]:
        # Groups are defined via read_group on registers; there is no config-level
        # mechanism to designate a group as writable, so this is always empty.
        return []

    @cached_property
    def _readable_registers(self) -> Sequence[ModbusRegisterDef]:
        # the list-comp-as-copy is technically not needed but it provides some protection
        # against accidental manipulation of the internal self.registers list.
        # the type hint (Sequence) also provides some type-checker protections
        # as Sequence is not mutable.
        return [reg for reg in self.registers]

    @cached_property
    def _readable_groups(self) -> list[str]:
        # All defined groups are readable — read_group is the only way to create a group.
        return list(self._group_index.keys())

    def _validate_protocol(self) -> None:
        """Reject configs whose ``protocol`` field is not ``"modbus"``."""
        if self.protocol != "modbus":
            raise ValueError(
                f"Config has protocol '{self.protocol}', expected 'modbus'. "
                f"Use the appropriate class for '{self.protocol}' configs."
            )

    def _validate_unique_register_names(self) -> None:
        """Reject duplicate register names."""
        names = [reg.name for reg in self.registers]
        seen = set()
        duplicates = set()
        for name in names:
            if name in seen:
                duplicates.add(name)
            seen.add(name)
        if duplicates:
            raise ValueError(f"Duplicate register names found: {sorted(duplicates)}")

    def _validate_no_register_overlap(self) -> None:
        """Reject overlapping address ranges within each register type."""
        by_type: dict[str, list[ModbusRegisterDef]] = {}
        for reg in self.registers:
            by_type.setdefault(reg.register_type, []).append(reg)

        for reg_type, regs in by_type.items():
            regs.sort(key=lambda x: x.starting_address)

            for i, reg1 in enumerate(regs):
                if reg_type in ("coil", "discrete"):
                    count1 = 1
                else:
                    count1 = reg1.register_count
                start1 = reg1.starting_address
                end1 = start1 + count1 - 1

                for reg2 in regs[i + 1 :]:
                    if reg_type in ("coil", "discrete"):
                        count2 = 1
                    else:
                        count2 = reg2.register_count
                    start2 = reg2.starting_address
                    end2 = start2 + count2 - 1

                    if start1 <= end2 and start2 <= end1:
                        raise ValueError(
                            f"Register overlap detected in {reg_type} registers: "
                            f"'{reg1.name}' (addresses {start1}-{end1}) overlaps with "
                            f"'{reg2.name}' (addresses {start2}-{end2})"
                        )

    def _validate_unique_bitmap_names(self) -> None:
        """Reject duplicate channel names across registers and their bitmap bits."""
        all_names = [reg.name for reg in self.registers]
        for reg in self.registers:
            if reg.bitmap:
                for bit in reg.bitmap:
                    all_names.append(bit.name)

        seen = set()
        duplicates = set()
        for name in all_names:
            if name in seen:
                duplicates.add(name)
            seen.add(name)
        if duplicates:
            raise ValueError(f"Duplicate names found (register or bitmap): {sorted(duplicates)}")

    def _validate_group_register_types(self) -> None:
        """Reject ``read_group``s mixing ``register_type``s or containing non-polled registers."""
        groups: dict[str, str] = {}
        for reg in self.registers:
            if reg.read_group is None:
                continue
            if not reg.poll:
                raise ValueError(
                    f"Register '{reg.name}' is in read_group '{reg.read_group}' but has poll=false. "
                    f"All registers in a read_group are read together and must have poll=true."
                )
            if reg.read_group in groups:
                if groups[reg.read_group] != reg.register_type:
                    raise ValueError(
                        f"Group '{reg.read_group}' contains mixed register types: "
                        f"'{groups[reg.read_group]}' and '{reg.register_type}'."
                    )
            else:
                groups[reg.read_group] = reg.register_type

    def _build_group_index(self) -> Mapping[str, Sequence[ModbusRegisterDef]]:
        """Build an index of group_id -> registers, sorted by starting_address."""
        groups: dict[str, list[ModbusRegisterDef]] = {}
        for reg in self.registers:
            if reg.read_group is not None:
                groups.setdefault(reg.read_group, []).append(reg)
        for regs in groups.values():
            regs.sort(key=lambda r: r.starting_address)
        return groups

    def _validate_group_span(self) -> None:
        """Reject groups whose address span exceeds the per-read limit (125 regs / 2000 bits)."""
        for group_id, regs in self._group_index.items():
            first = regs[0]
            last = regs[-1]
            is_bit_type = first.register_type in ("coil", "discrete")
            if is_bit_type:
                span = (last.starting_address + 1) - first.starting_address
                limit = MAX_COILS_PER_READ
            else:
                span = (last.starting_address + last.register_count) - first.starting_address
                limit = MAX_REGISTERS_PER_READ
            if span > limit:
                unit = "bits" if is_bit_type else "registers"
                raise ValueError(
                    f"Group '{group_id}' spans {span} {unit} "
                    f"(addresses {first.starting_address}-{last.starting_address + (0 if is_bit_type else last.register_count - 1)}), "
                    f"which exceeds the Modbus limit of {limit} {unit} per read."
                )

    def _validate_no_non_group_register_in_group_span(self) -> None:
        """Reject configs where a non-group register falls within a group's address span.

        A write_group call bulk-writes the entire address span from the first to the
        last register in the group. A non-member register within that span would be
        silently overwritten with 0/False.
        """
        for group_id, group_regs in self._group_index.items():
            # group_regs is sorted by starting_address — see _build_group_index
            first = group_regs[0]
            last = group_regs[-1]
            is_bit_type = first.register_type in ("coil", "discrete")
            group_start = first.starting_address
            group_end = last.starting_address if is_bit_type else last.starting_address + last.register_count - 1
            group_member_names = {r.name for r in group_regs}

            for reg in self.registers:
                if reg.name in group_member_names or reg.register_type != first.register_type:
                    continue
                reg_start = reg.starting_address
                reg_end = reg.starting_address if is_bit_type else reg.starting_address + reg.register_count - 1
                if reg_start <= group_end and reg_end >= group_start:
                    raise ValueError(
                        f"Register '{reg.name}' (addresses {reg_start}-{reg_end}) falls within "
                        f"the address span of group '{group_id}' (addresses {group_start}-{group_end}). "
                        f"A write_group call would overwrite this register with a default value."
                    )

    def get_group(self, group_id: str) -> Sequence[ModbusRegisterDef]:
        """Get all registers in a group, sorted by starting address."""
        regs = self._group_index.get(group_id)
        if regs is not None:
            return regs
        raise KeyError(f"Group '{group_id}' not found. Available: {list(self._group_index)}")

    @classmethod
    def from_json(cls, path: Path | str) -> ModbusConfig:
        """Load and validate a configuration from a JSON file."""
        import json

        path = Path(path)
        with open(path) as f:
            raw = json.load(f)

        return cls.model_validate(raw)

    def get_register(self, name: str) -> ModbusRegisterDef:
        """Return the register definition for ``name``. Raises ``KeyError`` if not found."""
        reg = self._register_index.get(name)
        if reg is not None:
            return reg
        raise KeyError(f"Register '{name}' not found. Available: {list(self._register_index)}")


class ModbusRegisterDriver(RegisterDriverBase):
    """Modbus implementation for register access driver.

    The driver owns the underlying modbus transport.
    By default this scanner is thread-safe with locks implemented at the driver level.
    If ``thread_safe`` is overridden to False, the Modbus connection resource will no
    longer be thread-safe and it will be up to the consuming code to ensure safety.
    """

    _modbus: ModbusDriver
    _config: ModbusConfig

    def __init__(self, configuration: ModbusConfig, *, thread_safe: bool = True) -> None:
        """Initialize a ModbusRegisterDriver instance.

        Args:
            configuration: A ModbusConfig instance, a dict (validated via Pydantic)
            thread_safe: If true (default), underlying driver is constructed as thread-safe.

        Raises:
            ValueError: If no connection is provided in the config
        """
        self._config = configuration
        self._modbus = ModbusDriver(configuration.connection, thread_safe=thread_safe)
        self._last_write_time: float = 0.0
        self._write_lock: threading.Lock | contextlib.nullcontext = (
            threading.Lock() if thread_safe else contextlib.nullcontext()
        )

    @property
    def writeable_registers(self) -> Sequence[ModbusRegisterDef]:
        return self._config._writeable_registers

    @property
    def writeable_groups(self) -> list[str]:
        return self._config._writeable_groups

    @property
    def readable_registers(self) -> Sequence[ModbusRegisterDef]:
        return self._config._readable_registers

    @property
    def readable_groups(self) -> list[str]:
        return self._config._readable_groups

    def enumerate_group_registers(self, group_id: str) -> list[str]:
        return [reg.name for reg in self._config.get_group(group_id)]

    def open(self) -> None:
        self._modbus.open()

    def close(self) -> None:
        self._modbus.close()

    @property
    def device_name(self) -> str:
        return self._config.device.name

    @property
    def unit_id(self) -> int:
        """Unit/slave ID from the connection configuration."""
        return self._modbus.unit_id

    def _read_register_decoded(self, reg: ModbusRegisterDef) -> register_value_type:
        """Read a register and decode based on data type."""
        match reg.register_type:
            case "holding":
                raw_regs = self._modbus.read_holding_registers(reg.starting_address, reg.register_count)
            case "input":
                raw_regs = self._modbus.read_input_registers(reg.starting_address, reg.register_count)
            case "coil":
                bits = self._modbus.read_coils(reg.starting_address, 1)
                return int(bits[0])
            case "discrete":
                bits = self._modbus.read_discrete_inputs(reg.starting_address, 1)
                return int(bits[0])
            case _:
                raise ValueError(f"Unknown register type: {reg.register_type}")
        return reg.decode_register_values(raw_regs)

    def read_raw_scaled(self, register_id: str) -> tuple[register_value_type, register_value_type]:
        register_def = self._config.get_register(register_id)
        decoded = self._read_register_decoded(register_def)
        scaled = register_def._apply_scaling(decoded)
        return (decoded, scaled)

    def read(self, register_id: str) -> register_value_type:
        """Read a register by name and publish the result.

        Args:
            register_id: Register alias as defined in the configuration.

        Returns:
            Value of register
        """
        _, scaled = self.read_raw_scaled(register_id)
        return scaled

    def build_extra_channels(
        self, register_id: str, raw: register_value_type, scaled: register_value_type
    ) -> dict[str, list[register_value_type]]:
        """Extract bitmap bit channels from a uint16 register, if any are defined."""
        reg = self._config.get_register(register_id)
        if reg.bitmap is None:
            return {}
        return {bit_def.name: [int((int(raw) >> bit_def.bit_index) & 1)] for bit_def in reg.bitmap}

    def read_group_raw_scaled(self, group_id: str) -> tuple[list[register_value_type], list[register_value_type]]:
        """Read a register group by name and return the associated data values.

        Args:
            group_id: Register group alias as defined in the configuration.

        Returns:
            Tuple of decoded register values and scaled values
        """
        regs = self._config.get_group(group_id)

        first = regs[0]
        last = regs[-1]
        start_address = first.starting_address
        is_bit_type = first.register_type in ("coil", "discrete")

        if is_bit_type:
            total_count = (last.starting_address + 1) - start_address
        else:
            total_count = (last.starting_address + last.register_count) - start_address

        raw_regs = []
        raw_bits = []
        match first.register_type:
            case "holding":
                raw_regs = self._modbus.read_holding_registers(start_address, total_count)
            case "input":
                raw_regs = self._modbus.read_input_registers(start_address, total_count)
            case "coil":
                raw_bits = self._modbus.read_coils(start_address, total_count)
            case "discrete":
                raw_bits = self._modbus.read_discrete_inputs(start_address, total_count)
            case _:
                raise ValueError(f"Unknown register type: {first.register_type}")

        decoded_registers = []
        scaled_registers = []
        for reg in regs:
            offset = reg.starting_address - start_address
            if is_bit_type:
                decoded_value: int | float = int(raw_bits[offset])
                scaled_value = decoded_value
            else:
                reg_slice = raw_regs[offset : offset + reg.register_count]
                decoded_value = reg.decode_register_values(reg_slice)
                scaled_value = reg._apply_scaling(decoded_value)
            decoded_registers.append(decoded_value)
            scaled_registers.append(scaled_value)

        return decoded_registers, scaled_registers

    def read_group(self, group_id: str) -> list[register_value_type]:
        """Read a register group by name and return the associated data values.

        Args:
            group_id: Register group alias as defined in the configuration.

        Returns:
            Scaled register values in group register-definition order.
        """
        _, scaled = self.read_group_raw_scaled(group_id)
        return scaled

    def _encode_and_write_register(self, reg: ModbusRegisterDef, raw_value: register_value_type | bool) -> None:
        """Write a value to a register, encoding based on data type."""
        match reg.register_type:
            case "holding":
                encoded_register_values = reg.encode_value_to_registers(raw_value)
                if len(encoded_register_values) == 1:
                    self._modbus.write_holding_register(reg.starting_address, encoded_register_values[0])
                else:
                    self._modbus.write_holding_registers(reg.starting_address, encoded_register_values)
            case "coil":
                self._modbus.write_coil(reg.starting_address, bool(raw_value))
            case "input" | "discrete":
                raise ValueError(f"Cannot write to read-only register type: {reg.register_type}")
            case _:
                raise ValueError(f"Unknown register type: {reg.register_type}")

    def _apply_write_delay(self) -> None:
        if self._config.timing is not None and self._config.timing.write_delay_ms > 0:
            delay_s = self._config.timing.write_delay_ms / 1000.0
            remaining_s = delay_s - (time.monotonic() - self._last_write_time)
            if remaining_s > 0:
                time.sleep(remaining_s)

    def write(self, register_id: str, value: register_value_type | str) -> register_value_type:
        """Write a value to a register by register_id and publish the command.

        Args:
            register_id: Register register_id as defined in the configuration.
            value: Value to write (in physical units if scaling is defined).
                For coil registers, pass True/False directly.
                For registers with a write_value_map, pass a string key to write
                the corresponding mapped value.

        Returns:
            Engineering value of the register written to the device
                This takes into account write_value_map strings and returns the value used

        Raises:
            TypeError: If value type doesn't match the register's data type.
            KeyError: If a string value is not found in the register's write_value_map.
            ValueError: If the register is read-only, the value violates ``write_min``/``write_max``,
                or the value or scaled raw is out of range for the data type.
        """
        reg = self._config.get_register(register_id)

        # Resolve string values through the register's write_value_map
        if isinstance(value, str):
            if reg.write_value_map is None:
                raise KeyError(
                    f"Register '{register_id}' has no write_value_map. "
                    f"Cannot write string '{value}' — pass a numeric value instead."
                )
            if value not in reg.write_value_map:
                raise KeyError(
                    f"'{value}' is not a valid value for register '{register_id}'. "
                    f"Available values: {list(reg.write_value_map.keys())}"
                )
            value = reg.write_value_map[value]

        reg._validate_write_value(value)

        if reg.scale is not None:
            raw_value: int | float = reg.scale.to_raw(value)
        else:
            raw_value = value

        raw_value = reg._validate_raw_value_range(raw_value)

        with self._write_lock:
            self._apply_write_delay()
            self._encode_and_write_register(reg, raw_value)
            self._last_write_time = time.monotonic()

        return value

    def write_group(self, group_id: str, values: list[register_value_type | str]) -> list[register_value_type]:
        """Write a register group by name and return the associated data values.

        Args:
            group_id: Register group alias as defined in the configuration.
            values: List of values in engineering units or enumeration strings

        Returns:
            List of values written to the hardware in engineering unit, removing all strings
        """
        regs = self._config.get_group(group_id)
        if len(regs) != len(values):
            raise ValueError(
                f"The specified group has {len(regs)} registers defined while only {len(values)} values were provided."
            )

        first = regs[0]
        last = regs[-1]
        start_address = first.starting_address
        is_bit_type = first.register_type in ("coil", "discrete")

        if is_bit_type:
            total_count = (last.starting_address + 1) - start_address
        else:
            total_count = (last.starting_address + last.register_count) - start_address

        encoded_boolcoils: list[bool] = []
        encoded_uintreg: list[int] = []
        if is_bit_type:
            encoded_boolcoils = [False] * total_count
        else:
            encoded_uintreg = [0] * total_count
        written: list[register_value_type] = []
        for reg, value in zip(regs, values):
            # NOTE: Addresses within this span that are not covered by any configured register
            # will be written with 0/False. If the device has a dense register map, it may
            # expect a non-zero value at a gap address — writing 0 could be incorrect. If the
            # device has a sparse register map, a gap in the config likely reflects an
            # undefined device register and is usually harmless. Some devices actively reject
            # writes to undefined/reserved registers, which would surface as a Modbus error;
            # there is no way to prevent this at the driver level.
            if isinstance(value, str):
                value = reg._string_to_value_map(value)
            offset = reg.starting_address - start_address
            if is_bit_type:
                encoded_boolcoils[offset] = bool(value)
            else:
                raw_value = reg.scale.to_raw(value) if reg.scale is not None else value
                encoded_value = reg.encode_value_to_registers(raw_value)
                encoded_uintreg[offset : offset + len(encoded_value)] = encoded_value
            written.append(value)

        with self._write_lock:
            self._apply_write_delay()
            match first.register_type:
                case "holding":
                    self._modbus.write_holding_registers(start_address, encoded_uintreg)
                case "coil":
                    self._modbus.write_coils(start_address, encoded_boolcoils)
                case "discrete" | "input":
                    raise ValueError(f"Cannot write to read-only register type: {first.register_type}")
                case _:
                    raise ValueError(f"Unknown register type: {first.register_type}")
            self._last_write_time = time.monotonic()

        return cast(list[register_value_type], values)  # this is forced by the processing loop above
