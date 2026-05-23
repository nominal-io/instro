"""I2C system definition: scaling, data formats, fields, registers, devices."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Literal, Optional


class ScalingFunction(ABC):
    """Convert between raw register integers and physical units (forward and inverse)."""

    @abstractmethod
    def to_physical(self, raw: int) -> float:
        """Raw register value → physical units."""
        pass

    @abstractmethod
    def to_raw(self, physical: float) -> int:
        """Physical value → raw integer. May raise ``NotImplementedError`` if inverse is not supported."""
        pass


class LinearScaling(ScalingFunction):
    """Linear scaling: ``physical = offset + gain * raw``.

    Example:
        >>> # Temperature sensor: 0.1°C per count, -40°C offset
        >>> LinearScaling(gain=0.1, offset=-40.0).to_physical(500)
        10.0
    """

    def __init__(self, gain: float = 1.0, offset: float = 0.0):
        self.gain = gain
        self.offset = offset

    def to_physical(self, raw: int) -> float:
        return self.offset + self.gain * float(raw)

    def to_raw(self, physical: float) -> int:
        return int(round((physical - self.offset) / self.gain))


class CustomScaling(ScalingFunction):
    """User-defined scaling. ``to_raw_fn`` is optional; ``to_raw`` raises ``NotImplementedError`` without it.

    Example:
        >>> CustomScaling(
        ...     to_physical_fn=lambda x: x / 4095 * 5.0 * 7.2,
        ...     to_raw_fn=lambda v: int(v / 7.2 / 5.0 * 4095),
        ... )
    """

    def __init__(
        self,
        to_physical_fn: Callable[[int], float],
        to_raw_fn: Callable[[float], int] | None = None,
    ):
        self._to_physical_fn = to_physical_fn
        self._to_raw_fn = to_raw_fn

    def to_physical(self, raw: int) -> float:
        return self._to_physical_fn(raw)

    def to_raw(self, physical: float) -> int:
        if self._to_raw_fn is None:
            raise NotImplementedError("Inverse transformation not provided for this CustomScaling")
        return self._to_raw_fn(physical)


@dataclass(frozen=True)
class DataFormat:
    """How to extract logical data from an I2C transfer and convert to physical units.

    Attributes:
        transfer_bits: Total bits transferred over I2C.
        data_width_bits: Logical data width if narrower than transfer_bits; ``None`` uses all.
        data_lsb: Starting bit position of the logical data.
        signed: Whether the logical data is 2's-complement signed.
        scaling: Optional scaling to convert raw → physical.
        units: Display string for the physical units.
    """

    transfer_bits: int
    data_width_bits: Optional[int] = None
    data_lsb: int = 0
    signed: bool = False
    scaling: Optional[ScalingFunction] = None
    units: str = ""

    @property
    def data_width_bytes(self) -> int:
        """Return number of bytes for transfer."""
        if self.transfer_bits % 8:
            raise ValueError("transfer_bits must be a multiple of 8")
        return self.transfer_bits // 8

    def _logical_width(self) -> int:
        """Return logical data width in bits."""
        return self.data_width_bits if self.data_width_bits else self.transfer_bits

    @staticmethod
    def _sign_extend(value: int, bit_width: int) -> int:
        """Interpret ``value`` as a 2's-complement ``bit_width``-bit signed integer.

        Examples:
            8-bit 0xFF → -1; 12-bit 0x800 → -2048; 4-bit 0b1011 → -5.
        """
        if bit_width <= 0:
            return 0
        sign_bit = 1 << (bit_width - 1)
        mask = (1 << bit_width) - 1
        value &= mask
        return (value ^ sign_bit) - sign_bit

    def extract_data(self, transfer_raw: int) -> int:
        """Slice the logical data field out of ``transfer_raw`` (sign-extended when ``signed``)."""
        width = self._logical_width()
        # Create a bitmask to isolate the desired logical data field and shift it into position
        mask = ((1 << width) - 1) << self.data_lsb
        # Apply mask and shift right to extract only logical data bits from the raw transfer
        raw = (transfer_raw & mask) >> self.data_lsb
        # If the format is signed, apply sign extension; otherwise just return the raw value
        return DataFormat._sign_extend(raw, width) if self.signed else raw

    def pack_data(self, data_raw: int) -> int:
        """Pack ``data_raw`` into the transfer container at ``data_lsb``; validates range vs ``signed``."""
        width = self._logical_width()

        if self.signed:
            minv = -(1 << (width - 1))
            maxv = (1 << (width - 1)) - 1
        else:
            minv = 0
            maxv = (1 << width) - 1
        if data_raw < minv or data_raw > maxv:
            raise ValueError("data value out of range")
        container = (data_raw & ((1 << width) - 1)) << self.data_lsb
        return container & ((1 << self.transfer_bits) - 1)

    def float_from_raw(self, transfer_raw: int) -> float:
        """Extract logical data and apply ``scaling``; falls back to raw float if no scaling is set."""
        data = self.extract_data(transfer_raw)
        if self.scaling:
            return self.scaling.to_physical(data)
        return float(data)

    def raw_from_float(self, physical: float) -> int:
        """Pack ``physical`` into a raw transfer value. Raises ``ValueError`` if out of range."""
        if self.scaling:
            data = self.scaling.to_raw(physical)
            return self.pack_data(data)

        # No scaling - treat physical as raw integer value
        width = self._logical_width()
        iv = int(round(physical))
        minv = 0
        maxv = (1 << width) - 1
        if iv < minv or iv > maxv:
            raise ValueError("value out of range")
        return self.pack_data(iv)


@dataclass(frozen=True)
class FieldDef:
    """A contiguous bit field within a register (e.g. ``FieldDef("mode", lsb=3, width_bits=2)`` for bits [4:3])."""

    name: str
    lsb: int
    width_bits: int = 1

    def mask(self) -> int:
        """Bitmask covering this field's bits (e.g. ``lsb=3, width=2`` → ``0b00011000``)."""
        return ((1 << self.width_bits) - 1) << self.lsb


@dataclass(frozen=True)
class RegisterDef:
    """I2C register: address, data format, power-on default, byte order, and named bit fields."""

    alias: str
    register: int
    default_value: int = 0x00
    format: DataFormat = field(default_factory=lambda: DataFormat(transfer_bits=8))
    endianness: Literal["little", "big"] = "big"
    fields: dict[str, FieldDef] = field(default_factory=dict)

    @property
    def data_width_bytes(self) -> int:
        return self.format.data_width_bytes

    def field(self, field_name: str) -> FieldDef:
        return self.fields[field_name]

    def extract_data(self, transfer_raw: int) -> int:
        return self.format.extract_data(transfer_raw)

    def pack_data(self, data_raw: int) -> int:
        return self.format.pack_data(data_raw)

    def float_from_raw(self, transfer_raw: int) -> float:
        return self.format.float_from_raw(transfer_raw)

    def raw_from_float(self, physical: float) -> int:
        return self.format.raw_from_float(physical)


@dataclass
class CommandDef:
    """A named command enum whose members are OR'd together when sent."""

    name: str
    values: type[Enum]


@dataclass
class RegisterDevice:
    """Register-based I2C device (e.g. a GPIO expander). Read/write by register alias."""

    name: str
    address: int  # 7-bit I2C address
    addr_width_bytes: int = 1
    registers: dict[str, RegisterDef] = field(default_factory=dict)

    def register(self, register_alias: str) -> RegisterDef:
        return self.registers[register_alias]


@dataclass
class CommandDevice:
    """Command-based I2C device (e.g. an ADC). Sends a command byte; no register addressing."""

    name: str
    address: int  # 7-bit I2C address
    data_format: DataFormat  # Required field, no default
    valid_commands: dict[str, CommandDef] = field(default_factory=dict)
    batch_commands: dict[str, list[Enum]] = field(default_factory=dict)
    endianness: Literal["little", "big"] = "big"

    _valid_enum_types: set[type] = field(default_factory=set, init=False)

    def __post_init__(self):
        """Cache the set of enum types accepted by ``valid_commands`` for fast validation."""
        self._valid_enum_types = {cmd_def.values for cmd_def in self.valid_commands.values()}

    def command(self, command_alias: str) -> CommandDef:
        return self.valid_commands[command_alias]

    @property
    def data_width_bytes(self) -> int:
        return self.data_format.data_width_bytes


Device = RegisterDevice | CommandDevice  # Union type for device variants


@dataclass
class SystemDefinition:
    """Top-level container of all I2C device definitions, passed to ``I2CInterface``.

    >>> system = SystemDefinition()
    >>> system.add_device(RegisterDevice(name="power_gpio", address=0x20))
    >>> system.add_device(CommandDevice(name="adc", address=0x09, data_format=DataFormat(...)))
    >>> i2c = I2CInterface(
    ...     name="main_i2c",
    ...     driver=Aardvark(serial_number="123456"),
    ...     system_definition=system,
    ... )
    >>> i2c.write("power_gpio", "LED_OUTPUT_STATE", 0xFF)
    """

    devices: dict[str, Device] = field(default_factory=dict)  # periph -> Device

    def add_device(self, dev: Device) -> None:
        self.devices[dev.name] = dev

    def device(self, periph: str) -> Device:
        return self.devices[periph]
