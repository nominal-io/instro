"""Shared types: runtime dataclasses (Data/Measurement/Command) and cross-protocol Pydantic configs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# Runtime data types
# ============================================================================


@dataclass
class BackgroundDaemonConfig:
    interval: float = 1.0


class DataType(str, Enum):
    MEASUREMENT = "measurement"
    COMMAND = "command"


@dataclass(frozen=True)
class Data:
    """Channel data on a common timebase; ``type`` tells publishers what produced it."""

    channel_data: dict[str, list[float] | list[str]]
    timestamps: list[int]
    tags: dict[str, str] | None = None
    type: DataType = field(kw_only=True)

    @staticmethod
    def create_timestamps_from_dt(t0: int, dt: int, length: int, backstamp: bool) -> list[int]:
        """Build a ``length``-long timestamp list at ``dt`` ns spacing starting at ``t0`` (ns since epoch).

        With ``backstamp=True``, shift ``t0`` back by ``dt * (length - 1)`` so the
        last sample lands at the original ``t0`` (useful when ``t0`` is the
        completion time of a finite acquisition).
        """
        if backstamp:
            t0 = t0 - dt * (length - 1)
        return [t0 + i * dt for i in range(length)]

    def _get_values(self) -> list[float] | list[str]:
        if len(self.channel_data) != 1:
            raise ValueError("Multiple channels present. Use channel_data directly and index for the desired channel.")

        return next(iter(self.channel_data.values()))

    @property
    def values(self) -> list[float] | list[str]:
        """Values for the only channel; raises ``ValueError`` if the Data holds multiple channels."""
        return self._get_values()

    @property
    def latest(self) -> float | str:
        """Most recent value of the only channel; raises ``ValueError`` if the Data holds multiple."""
        return self._get_values()[-1]

    def _get_channel(self, channel: str) -> Data:
        """Return a copy holding only ``channel`` (with the original timestamps and tags)."""
        if channel not in self.channel_data:
            raise KeyError(f"Channel '{channel}' not found in channel_data.")

        return replace(
            self,
            channel_data={channel: self.channel_data[channel]},
            timestamps=self.timestamps.copy(),
            tags=self.tags.copy() if self.tags is not None else None,
        )


@dataclass(frozen=True)
class Measurement(Data):
    """Data read from an instrument."""

    type: DataType = field(default=DataType.MEASUREMENT, init=False)


@dataclass(frozen=True)
class Command(Data):
    """Data written to an instrument: one point per channel."""

    type: DataType = field(default=DataType.COMMAND, init=False)

    def __init__(
        self,
        channel_data: Mapping[str, list[float] | list[str] | float | str],
        timestamps: list[int] | None = None,
        tags: dict[str, str] | None = None,
        *,
        timestamp: int | None = None,
    ):
        # Pre-Data callers pass a scalar per channel and ``timestamp=``; normalize to the Data shape.
        if timestamp is not None:
            timestamps = [timestamp]
        if timestamps is None:
            raise TypeError("Command requires 'timestamps' or 'timestamp'")
        channels = {name: value if isinstance(value, list) else [value] for name, value in channel_data.items()}
        if len(timestamps) != 1 or any(len(values) != 1 for values in channels.values()):
            raise ValueError("Command holds exactly one point per channel")
        Data.__init__(
            self, cast("dict[str, list[float] | list[str]]", channels), timestamps, tags, type=DataType.COMMAND
        )

    @property
    def timestamp(self) -> int:
        return self.timestamps[0]


# ============================================================================
# Protocol configuration types
#
# Pydantic models reused across protocol implementations. When adding a new
# protocol, import these rather than redefining them. To extend a type for
# protocol-specific behavior, subclass it in that protocol's own types module.
# ============================================================================


class DeviceInfo(BaseModel):
    """Device metadata. ``name`` is the channel-name prefix on publish (e.g. ``my_device.temperature``)."""

    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    manufacturer: str = ""
    model: str = ""


class LinearScale(BaseModel):
    """Linear scaling: physical = offset + (gain * raw).

    Applied automatically on reads (raw -> physical) and reversed on writes
    (physical -> raw). Not all protocols or data point types support scaling --
    check the protocol-specific documentation.
    """

    type: Literal["linear"] = "linear"
    gain: float = Field(default=1.0, description="Scale factor (must not be zero)")
    offset: float = 0.0

    def model_post_init(self, __context) -> None:
        """Validate that gain is not zero."""
        if self.gain == 0:
            raise ValueError("LinearScale gain must not be zero (would cause division by zero)")

    def to_physical(self, raw: float) -> float:
        """Convert raw register value to physical units."""
        return self.offset + self.gain * raw

    def to_raw(self, physical: float) -> float:
        """Convert physical value to raw register value.

        Returns float to preserve precision for float registers.
        Integer registers should handle conversion in _encode_value.
        """
        return (physical - self.offset) / self.gain


# Union of all supported scale types. Extend this when adding new scaling
# strategies (e.g., PolynomialScale, LookupTableScale).
ScaleType = LinearScale
