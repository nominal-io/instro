"""Configuration types for the unstable EtherNet/IP instrument."""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from instro.lib.types import DeviceInfo

__all__ = [
    "DeviceInfo",
    "EtherNetIPBackplaneHop",
    "EtherNetIPConfig",
    "EtherNetIPConnectionInfo",
    "EtherNetIPRoutePath",
    "TagDef",
    "TimingConfig",
]


INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "sint": (-128, 127),
    "int": (-32768, 32767),
    "dint": (-2147483648, 2147483647),
    "lint": (-9223372036854775808, 9223372036854775807),
    "usint": (0, 255),
    "uint": (0, 65535),
    "udint": (0, 4294967295),
    "ulint": (0, 18446744073709551615),
}
INTEGER_DATA_TYPES = tuple(INTEGER_RANGES)
FLOAT_DATA_TYPES = ("real", "lreal")
BOOL_DATA_TYPES = ("bool",)
STREAMABLE_DATA_TYPES = BOOL_DATA_TYPES + INTEGER_DATA_TYPES + FLOAT_DATA_TYPES

WriteValue = bool | int | float
RouteSlot = Annotated[int, Field(ge=0, le=255, description="Backplane slot number; CIP port 1 is implied.")]

DataType = Literal[
    "bool",
    "sint",
    "int",
    "dint",
    "lint",
    "usint",
    "uint",
    "udint",
    "ulint",
    "real",
    "lreal",
]


class EtherNetIPBaseModel(BaseModel):
    """Base model for EtherNet/IP config sections.

    Forbids unknown entries at every EtherNet/IP config layer without
    repeating the same model_config setting on each model.
    """

    model_config = ConfigDict(extra="forbid")


class TimingConfig(EtherNetIPBaseModel):
    """Timing configuration for EtherNet/IP polling."""

    poll_interval: float = Field(ge=0.01, le=10.0, description="Polling interval in seconds")


class EtherNetIPBackplaneHop(EtherNetIPBaseModel):
    """One supported EtherNet/IP backplane route hop.

    CIP port 1 is implied for backplane hops; the config only exposes the slot.
    """

    type: Literal["backplane"] = Field(description="Route hop kind.")
    slot: RouteSlot


class EtherNetIPRoutePath(EtherNetIPBaseModel):
    """Ordered EtherNet/IP route path supported by the native backend."""

    hops: list[EtherNetIPBackplaneHop] = Field(
        default_factory=list,
        description="Ordered route hops. Only backplane hops are supported by the native backend today.",
    )


class EtherNetIPConnectionInfo(EtherNetIPBaseModel):
    """EtherNet/IP TCP endpoint configuration."""

    host: str
    port: int = Field(default=44818, ge=1, le=65535)
    route_path: EtherNetIPRoutePath | None = None

    @property
    def address(self) -> str:
        """Endpoint string accepted by the native EtherNet/IP session."""
        return f"{self.host}:{self.port}"


class TagDef(EtherNetIPBaseModel):
    """Definition of one EtherNet/IP tag exposed as an instrument channel."""

    alias: str = Field(description="Unique local Nominal channel alias for this tag")
    tag_name: str = Field(description="PLC tag name to read/write")
    description: str | None = None
    data_type: DataType = Field(description="PLC scalar data type for this tag")
    poll: bool = Field(default=True, description="Include this tag in background polling")
    write_min: float | int | None = None
    write_max: float | int | None = None

    @property
    def expected_plc_kind_name(self) -> str:
        """Return the native PlcKind member name expected for this tag."""
        return self.data_type.upper()

    def validate_write_value(self, value: WriteValue) -> None:
        """Validate a user-provided write value against this tag definition."""
        if self.data_type in BOOL_DATA_TYPES:
            if isinstance(value, bool):
                return
            if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
                return
            raise TypeError(f"Tag '{self.alias}' is a bool type but got {type(value).__name__} value {value!r}.")

        if self.data_type in INTEGER_DATA_TYPES:
            int_value = self.validate_integer_raw_value(value)
            self._validate_numeric_write_limits(int_value)
            return

        if self.data_type in FLOAT_DATA_TYPES:
            if isinstance(value, bool):
                raise TypeError(f"Tag '{self.alias}' is a float type ({self.data_type}) but got bool.")
            if not isinstance(value, int | float):
                raise TypeError(
                    f"Tag '{self.alias}' is a float type ({self.data_type}) but got {type(value).__name__}."
                )
            self._validate_numeric_write_limits(value)
            return

    def _validate_numeric_write_limits(self, value: int | float) -> None:
        if self.write_min is not None and value < self.write_min:
            raise ValueError(f"Tag '{self.alias}' value {value} is below write_min ({self.write_min}).")
        if self.write_max is not None and value > self.write_max:
            raise ValueError(f"Tag '{self.alias}' value {value} is above write_max ({self.write_max}).")

    def validate_integer_raw_value(self, raw_value: WriteValue, data_type: str | None = None) -> int:
        """Validate and coerce a raw integer value for this tag's PLC type."""
        target_data_type = data_type or self.data_type

        if isinstance(raw_value, bool):
            raise TypeError(f"Tag '{self.alias}' is an integer type ({target_data_type}) but got bool.")
        if isinstance(raw_value, float):
            raise TypeError(f"Tag '{self.alias}' is an integer type ({target_data_type}) but got float {raw_value}.")
        if not isinstance(raw_value, int):
            raise TypeError(
                f"Tag '{self.alias}' is an integer type ({target_data_type}) but got {type(raw_value).__name__}."
            )

        ranges = INTEGER_RANGES.get(target_data_type)
        if ranges is None:
            raise ValueError(f"Tag '{self.alias}' has non-integer data_type '{target_data_type}'.")

        min_val, max_val = ranges
        int_value = int(raw_value)
        if int_value < min_val or int_value > max_val:
            raise ValueError(f"Tag '{self.alias}' raw value {int_value} is out of range for {target_data_type}.")
        return int_value

    def validate_streamable_read(self, actual_kind: object) -> None:
        """Validate that this tag can be published as measurement data."""
        if self.data_type in STREAMABLE_DATA_TYPES:
            return
        raise TypeError(
            f"Tag '{self.alias}' returned PLC kind {actual_kind!r}, which is not supported as publishable "
            "measurement data. Supported read data types are bool and numeric PLC scalar types."
        )

    @model_validator(mode="after")
    def _validate_tag_constraints(self) -> "TagDef":
        if (self.write_min is not None or self.write_max is not None) and self.data_type not in (
            INTEGER_DATA_TYPES + FLOAT_DATA_TYPES
        ):
            raise ValueError(
                f"write_min/write_max are only supported for numeric tags, got data_type='{self.data_type}'"
            )

        if self.write_min is not None and self.write_max is not None and self.write_min > self.write_max:
            raise ValueError(f"write_min ({self.write_min}) must be less than or equal to write_max ({self.write_max})")

        self._validate_numeric_write_limit_ranges()

        return self

    def _validate_numeric_write_limit_ranges(self) -> None:
        if self.data_type not in INTEGER_RANGES:
            return

        min_val, max_val = INTEGER_RANGES[self.data_type]
        for field_name, field_val in (("write_min", self.write_min), ("write_max", self.write_max)):
            if field_val is None:
                continue

            if field_val < min_val or field_val > max_val:
                raise ValueError(
                    f"{field_name} ({field_val}) is out of range for {self.data_type} [{min_val}, {max_val}]"
                )


class EtherNetIPConfig(EtherNetIPBaseModel):
    """Complete unstable EtherNet/IP instrument configuration."""

    version: int = 1
    protocol: str = "ethernetip"
    device: DeviceInfo
    timing: TimingConfig | None = None
    connection: EtherNetIPConnectionInfo | None = None
    tags: list[TagDef] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        """Validate the top-level config."""
        self._validate_protocol()
        self._validate_unique_tag_aliases()
        self._validate_unique_tag_names()

    @cached_property
    def _tag_index(self) -> dict[str, TagDef]:
        return {tag.alias: tag for tag in self.tags}

    @property
    def polled_tags(self) -> list[TagDef]:
        """Tags included in background polling."""
        return [tag for tag in self.tags if tag.poll]

    def _validate_protocol(self) -> None:
        if self.protocol != "ethernetip":
            raise ValueError(
                f"Config has protocol '{self.protocol}', expected 'ethernetip'. "
                f"Use the appropriate class for '{self.protocol}' configs."
            )

    def _validate_unique_tag_aliases(self) -> None:
        aliases = [tag.alias for tag in self.tags]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for alias in aliases:
            if alias in seen:
                duplicates.add(alias)
            seen.add(alias)
        if duplicates:
            raise ValueError(f"Duplicate tag aliases found: {sorted(duplicates)}")

    def _validate_unique_tag_names(self) -> None:
        tag_names = [tag.tag_name for tag in self.tags]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for tag_name in tag_names:
            if tag_name in seen:
                duplicates.add(tag_name)
            seen.add(tag_name)
        if duplicates:
            raise ValueError(f"Duplicate tag names found: {sorted(duplicates)}")

    @classmethod
    def from_json(cls, path: Path | str) -> "EtherNetIPConfig":
        """Load configuration from a JSON file."""
        with open(Path(path)) as f:
            raw = json.load(f)
        return cls.model_validate(raw)

    def get_tag(self, alias: str) -> TagDef:
        """Get a tag definition by local alias."""
        tag = self._tag_index.get(alias)
        if tag is not None:
            return tag
        raise KeyError(f"Tag alias '{alias}' not found. Available: {list(self._tag_index)}")
