"""I2C JSON config (Pydantic). ``I2CConfig`` describes one bus and builds the ``SystemDefinition`` the interface runs on."""

from __future__ import annotations

import json
import warnings
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from instro.i2c.types import (
    CommandDef,
    CommandDevice,
    DataFormat,
    FieldDef,
    LinearScaling,
    RegisterDef,
    RegisterDevice,
    SystemDefinition,
)
from instro.lib.config import TimingConfig
from instro.lib.types import DeviceInfo, LinearScale

if TYPE_CHECKING:
    from instro.i2c.i2c import I2CDriverBase

__all__ = [
    "BatchCommandConfig",
    "CommandDeviceConfig",
    "DataFormatConfig",
    "DeviceInfo",
    "I2CConfig",
    "RegisterConfig",
    "RegisterDeviceConfig",
    "TimingConfig",
]


def _parse_int(value: object) -> object:
    """Accept hex/binary string literals (``"0x48"``, ``"0b1010"``) alongside JSON integers."""
    if isinstance(value, bool):
        raise ValueError("expected an integer or a string literal such as '0x48', got a bool")
    if isinstance(value, str):
        return int(value, 0)
    return value


IntLike = Annotated[int, BeforeValidator(_parse_int), Field(ge=0)]
I2CAddress = Annotated[int, BeforeValidator(_parse_int), Field(ge=0, le=0x7F, description="7-bit I2C address")]


# ============ Connection Config (private: the declarative path only) ============


class _AardvarkConnectionConfig(BaseModel):
    """Declarative Total Phase Aardvark connection block. Private: users construct ``Aardvark`` directly."""

    model_config = ConfigDict(extra="forbid")
    interface: Literal["aardvark"] = "aardvark"
    serial_number: str | None = Field(
        default=None, description="Adapter serial number (e.g. '2239-764425'); omit to use the first adapter found."
    )

    def build(self) -> I2CDriverBase:
        """Build the driver this block describes. The vendor package is imported here, not at config-load time."""
        try:
            from instro.i2c.drivers.totalphase import Aardvark
        except ImportError as e:
            raise ImportError(
                "connection.interface 'aardvark' needs the Aardvark vendor package; "
                "install it with `pip install 'instro[i2c]'` (or `uv sync --extra i2c`)."
            ) from e
        return Aardvark(serial_number=self.serial_number)


# New adapters join this union as further ``interface``-tagged blocks.
_ConnectionConfig = Annotated[_AardvarkConnectionConfig, Field(discriminator="interface")]


# ============ Data Format ============


class DataFormatConfig(BaseModel):
    """How to extract logical data from an I2C transfer and scale it. Builds a :class:`~instro.i2c.types.DataFormat`."""

    model_config = ConfigDict(extra="forbid")
    transfer_bits: int = Field(gt=0, description="Total bits transferred over I2C; a multiple of 8.")
    data_width_bits: int | None = Field(
        default=None, ge=1, description="Logical data width when narrower than transfer_bits; omit to use all bits."
    )
    data_lsb: int = Field(default=0, ge=0, description="Bit position of the logical data's LSB within the transfer.")
    signed: bool = Field(default=False, description="Interpret the logical data as two's-complement.")
    scaling: LinearScale | None = Field(default=None, description="Raw-to-physical scaling; linear only in JSON.")
    units: str = Field(default="", description="Display string for the physical units.")

    @model_validator(mode="after")
    def _validate_layout(self) -> DataFormatConfig:
        if self.transfer_bits % 8:
            raise ValueError(f"transfer_bits ({self.transfer_bits}) must be a multiple of 8")
        if self.data_lsb + self.logical_width > self.transfer_bits:
            raise ValueError(
                f"data_lsb ({self.data_lsb}) + data width ({self.logical_width}) exceeds "
                f"transfer_bits ({self.transfer_bits})"
            )
        return self

    @property
    def logical_width(self) -> int:
        """Logical data width in bits."""
        return self.data_width_bits if self.data_width_bits else self.transfer_bits

    def build(self) -> DataFormat:
        """Build the runtime ``DataFormat``; a ``LinearScale`` block becomes ``LinearScaling``."""
        scaling = LinearScaling(gain=self.scaling.gain, offset=self.scaling.offset) if self.scaling else None
        return DataFormat(
            transfer_bits=self.transfer_bits,
            data_width_bits=self.data_width_bits,
            data_lsb=self.data_lsb,
            signed=self.signed,
            scaling=scaling,
            units=self.units,
        )


# ============ Register Devices ============


with warnings.catch_warnings():
    # The field is named ``register`` to match ``RegisterDef.register``; Pydantic warns because the
    # name also shadows ``ABCMeta.register`` on ``BaseModel``, which nothing here relies on.
    warnings.filterwarnings("ignore", message='Field name "register"', category=UserWarning)

    class RegisterConfig(BaseModel):
        """One register on a register-based device. Builds a :class:`~instro.i2c.types.RegisterDef`."""

        model_config = ConfigDict(extra="forbid")
        alias: str = Field(description="Register name used in read()/write() calls and channel names.")
        register: IntLike = Field(description="Register address; must fit in the device's addr_width_bytes.")
        default_value: IntLike = Field(default=0, description="Power-on default written back by reset_reg().")
        format: DataFormatConfig = Field(default_factory=lambda: DataFormatConfig(transfer_bits=8))
        endianness: Literal["little", "big"] = "big"
        fields: list[FieldDef] = Field(default_factory=list, description="Named bit fields within the register.")
        poll: bool = Field(default=False, description="Read this register from the background daemon.")

        @model_validator(mode="after")
        def _validate_register(self) -> RegisterConfig:
            if self.default_value >= 1 << self.format.transfer_bits:
                raise ValueError(
                    f"register '{self.alias}': default_value {self.default_value:#x} does not fit in "
                    f"{self.format.transfer_bits} transfer bits"
                )
            seen: set[str] = set()
            for f in self.fields:
                if f.name in seen:
                    raise ValueError(f"register '{self.alias}': duplicate field name '{f.name}'")
                seen.add(f.name)
                if f.lsb < 0 or f.width_bits < 1:
                    raise ValueError(f"register '{self.alias}': field '{f.name}' needs lsb >= 0 and width_bits >= 1")
                if f.lsb + f.width_bits > self.format.transfer_bits:
                    raise ValueError(
                        f"register '{self.alias}': field '{f.name}' (bits {f.lsb}..{f.lsb + f.width_bits - 1}) "
                        f"exceeds {self.format.transfer_bits} transfer bits"
                    )
            return self

        def build(self) -> RegisterDef:
            """Build the runtime ``RegisterDef``."""
            return RegisterDef(
                alias=self.alias,
                register=self.register,
                default_value=self.default_value,
                format=self.format.build(),
                endianness=self.endianness,
                fields={f.name: f for f in self.fields},
            )


class RegisterDeviceConfig(BaseModel):
    """A register-based I2C device. Builds a :class:`~instro.i2c.types.RegisterDevice`."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["register"] = "register"
    name: str = Field(description="Device name used as the peripheral argument and channel-name component.")
    address: I2CAddress
    addr_width_bytes: Literal[1, 2] = 1
    registers: list[RegisterConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_registers(self) -> RegisterDeviceConfig:
        limit = 1 << (8 * self.addr_width_bytes)
        seen: set[str] = set()
        for reg in self.registers:
            if reg.alias in seen:
                raise ValueError(f"device '{self.name}': duplicate register alias '{reg.alias}'")
            seen.add(reg.alias)
            if reg.register >= limit:
                raise ValueError(
                    f"device '{self.name}': register '{reg.alias}' address {reg.register:#x} does not fit in "
                    f"addr_width_bytes={self.addr_width_bytes}"
                )
        return self

    def build(self) -> RegisterDevice:
        """Build the runtime ``RegisterDevice``."""
        return RegisterDevice(
            name=self.name,
            address=self.address,
            addr_width_bytes=self.addr_width_bytes,
            registers={reg.alias: reg.build() for reg in self.registers},
        )


# ============ Command Devices ============


class BatchCommandConfig(BaseModel):
    """A named batch command: one member from each referenced command group, OR'd together when sent."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Batch command name used in query() calls and channel names.")
    commands: dict[str, str] = Field(description="Command group -> member name; one member per group.")
    poll: bool = Field(default=False, description="Query this batch command from the background daemon.")


class CommandDeviceConfig(BaseModel):
    """A command-based I2C device. Builds a :class:`~instro.i2c.types.CommandDevice`."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["command"] = "command"
    name: str = Field(description="Device name used as the peripheral argument and channel-name component.")
    address: I2CAddress
    endianness: Literal["little", "big"] = "big"
    data_format: DataFormatConfig
    commands: dict[str, dict[str, IntLike]] = Field(
        default_factory=dict, description="Command group -> {member name: command byte}."
    )
    batch_commands: list[BatchCommandConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_commands(self) -> CommandDeviceConfig:
        for group, members in self.commands.items():
            for member, value in members.items():
                if value > 0xFF:
                    raise ValueError(
                        f"device '{self.name}': command {group}.{member} value {value:#x} does not fit in one byte"
                    )
        seen: set[str] = set()
        for batch in self.batch_commands:
            if batch.name in seen:
                raise ValueError(f"device '{self.name}': duplicate batch command name '{batch.name}'")
            seen.add(batch.name)
            for group, member in batch.commands.items():
                if group not in self.commands:
                    raise ValueError(
                        f"device '{self.name}': batch command '{batch.name}' references unknown command group "
                        f"'{group}' (available: {list(self.commands)})"
                    )
                if member not in self.commands[group]:
                    raise ValueError(
                        f"device '{self.name}': batch command '{batch.name}' references unknown member "
                        f"'{member}' of command group '{group}' (available: {list(self.commands[group])})"
                    )
        return self

    def build(self) -> CommandDevice:
        """Build the runtime ``CommandDevice``; each command group becomes an ``Enum``."""
        enums: dict[str, type[Enum]] = {
            group: cast(type[Enum], Enum(group, members)) for group, members in self.commands.items()
        }
        return CommandDevice(
            name=self.name,
            address=self.address,
            data_format=self.data_format.build(),
            valid_commands={group: CommandDef(name=group, values=enum) for group, enum in enums.items()},
            batch_commands={
                batch.name: [enums[group][member] for group, member in batch.commands.items()]
                for batch in self.batch_commands
            },
            endianness=self.endianness,
        )


DeviceConfig = Annotated[RegisterDeviceConfig | CommandDeviceConfig, Field(discriminator="type")]


# ============ Top-Level Config ============


class I2CConfig(BaseModel):
    """Complete I2C bus configuration. Load from JSON via ``I2CConfig.from_json(path)``.

    Example::

        config = I2CConfig(
            device=DeviceInfo(name="sensor_bus"),
            timing=TimingConfig(poll_interval=1.0),
            devices=[
                RegisterDeviceConfig(
                    name="temp_sensor",
                    address=0x48,
                    registers=[
                        RegisterConfig(
                            alias="temperature",
                            register=0x00,
                            format=DataFormatConfig(
                                transfer_bits=16, signed=True, scaling=LinearScale(gain=0.0625), units="°C"
                            ),
                            poll=True,
                        )
                    ],
                )
            ],
        )
        i2c = I2CInterface(config=config, driver=Aardvark(serial_number="2239-764425"))
    """

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    protocol: Literal["i2c"] = "i2c"
    device: DeviceInfo
    timing: TimingConfig | None = None
    connection: _ConnectionConfig | None = None
    devices: list[DeviceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_devices(self) -> I2CConfig:
        names: set[str] = set()
        addresses: dict[int, str] = {}
        for dev in self.devices:
            if dev.name in names:
                raise ValueError(f"duplicate device name '{dev.name}'")
            names.add(dev.name)
            if dev.address in addresses:
                raise ValueError(
                    f"devices '{addresses[dev.address]}' and '{dev.name}' share I2C address {dev.address:#04x}"
                )
            addresses[dev.address] = dev.name
        return self

    @classmethod
    def from_json(cls, path: Path | str) -> I2CConfig:
        """Load and validate a configuration from a JSON file."""
        with open(Path(path)) as f:
            return cls.model_validate(json.load(f))

    def build_system_definition(self) -> SystemDefinition:
        """Build the runtime ``SystemDefinition`` described by ``devices``."""
        return SystemDefinition(devices={dev.name: dev.build() for dev in self.devices})
