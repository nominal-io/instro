"""Modbus configuration types (Pydantic). ``DeviceInfo``/``LinearScale``/``ScaleType`` come from ``instro.lib.types``."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from instro.lib.types import (
    DeviceInfo,
    LinearScale,
    ScaleType,
)

# Re-export shared types so existing imports from this module continue to work.
__all__ = [
    "DeviceInfo",
    "LinearScale",
    "ScaleType",
    "ModbusConfig",
    "TimingConfig",
    "TCPConnection",
    "RTUConnection",
    "RegisterDef",
    "BitDef",
]


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


# ============ Register Definition ============


class RegisterDef(BaseModel):
    """Definition of a Modbus register.

    Note:
        - Swap options control byte ordering for multi-byte values:
          - `byte_swap`: swap bytes within each 16-bit word
          - `word_swap`: swap 16-bit words (for 32-bit and 64-bit types)
          - `long_swap`: swap 32-bit longs (for 64-bit types only)
        - All swap options default to False (big-endian / network byte order).
        - `scale` is not allowed for coils and discrete inputs.
    """

    name: str = Field(description="Unique name/alias for this register")
    description: str | None = None
    starting_address: int = Field(ge=0, le=65535)
    register_type: Literal["holding", "input", "coil", "discrete"] = "holding"
    data_type: DataType = "uint16"
    byte_swap: bool = False
    word_swap: bool = False
    long_swap: bool = False
    scale: ScaleType | None = None
    bitmap: list[BitDef] | None = None
    poll: bool = True
    write_min: float | int | None = None
    write_max: float | int | None = None
    write_value_map: dict[str, int | float] | None = None
    read_group: str | None = None

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
    def _validate_register_type_constraints(self) -> "RegisterDef":
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


# ============ Top-Level Config ============


class ModbusConfig(BaseModel):
    """Complete Modbus device configuration. Load from JSON via ``ModbusConfig.from_json(path)``."""

    version: int = 1
    protocol: str = "modbus"
    device: DeviceInfo
    timing: TimingConfig | None = None
    connection: ConnectionType | None = Field(default=None, discriminator="transport")
    registers: list[RegisterDef] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        """Cross-register validation: uniqueness, overlap, groups."""
        self._validate_protocol()
        self._validate_unique_register_names()
        self._validate_unique_bitmap_names()
        self._validate_no_register_overlap()
        self._validate_group_register_types()
        self._validate_group_span()

    # `registers` is effectively immutable after __init__ (no callers mutate it), so both
    # indices are safe to cache. These are hit on every background poll via _read_group,
    # so rebuilding on every access was real wasted work.
    @cached_property
    def _register_index(self) -> dict[str, RegisterDef]:
        return {reg.name: reg for reg in self.registers}

    @cached_property
    def _group_index(self) -> dict[str, list[RegisterDef]]:
        return self._build_group_index()

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
        by_type: dict[str, list[RegisterDef]] = {}
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

    def _build_group_index(self) -> dict[str, list[RegisterDef]]:
        """Index ``read_group`` → registers, sorted by ``starting_address``."""
        groups: dict[str, list[RegisterDef]] = {}
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

    def get_group(self, group_id: str) -> list[RegisterDef]:
        """Return the registers in ``group_id``, sorted by ``starting_address``."""
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

    def get_register(self, name: str) -> RegisterDef:
        """Return the register definition for ``name``. Raises ``KeyError`` if not found."""
        reg = self._register_index.get(name)
        if reg is not None:
            return reg
        raise KeyError(f"Register '{name}' not found. Available: {list(self._register_index)}")
