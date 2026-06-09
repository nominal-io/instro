# This code is derived from concepts in the py-lab-hal codebase (Apache 2.0 licensed)
# Original py-lab-hal repository: https://github.com/google/py-lab-hal

"""Shared types: runtime dataclasses (Measurement/Command) and cross-protocol Pydantic configs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

# ============================================================================
# Runtime data types
# ============================================================================


@dataclass
class BackgroundDaemonConfig:
    interval: float = 1.0


@dataclass
class Measurement:
    """Data structure to hold measurement data. All channels have a common timebase."""

    channel_data: dict[str, list[float]]
    timestamps: list[int]
    tags: dict[str, str] | None = None

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
        """Values for the only channel; raises ``ValueError`` if the Measurement holds multiple channels."""
        return self._get_values()

    @property
    def latest(self) -> float | str:
        """Most recent value of the only channel; raises ``ValueError`` if the Measurement holds multiple."""
        return self._get_values()[-1]

    def _get_channel(self, channel: str) -> "Measurement":
        """Return a new Measurement holding only ``channel`` (with the original timestamps and tags)."""
        if channel not in self.channel_data:
            raise KeyError(f"Channel '{channel}' not found in channel_data.")

        # Make a new Measurement with just this channel's data, same timestamps and tags
        return Measurement(
            channel_data={channel: self.channel_data[channel]},
            timestamps=self.timestamps.copy(),
            tags=self.tags.copy() if self.tags is not None else None,
        )


@dataclass
class Command:
    """Data structure to hold command data."""

    # Same as Measurement, but with a single datapoint per channel

    channel_data: dict[str, float | str]
    timestamp: int
    tags: dict[str, str] | None = None


# ============================================================================
# Protocol configuration types
#
# Pydantic models reused across protocol implementations. When adding a new
# protocol, import these rather than redefining them. To extend a type for
# protocol-specific behavior, subclass it in that protocol's own types module.
# ============================================================================


class DeviceInfo(BaseModel):
    """Device metadata. ``name`` is the channel-name prefix on publish (e.g. ``my_device.temperature``)."""

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
