"""Pydantic config for JSON config-driven InstroMavStream construction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from instro.lib.config import PublisherConfigType, TimingConfig
from instro.lib.types import DeviceInfo

__all__ = ["ConnectionConfig", "MavStreamConfig"]


class ConnectionConfig(BaseModel):
    """Connection info for a MAVLink stream."""

    model_config = ConfigDict(extra="forbid")
    address: str = Field(description="pymavlink connection string, e.g. 'udp:0.0.0.0:14550' or 'tcp:127.0.0.1:5760'.")


class MavStreamConfig(BaseModel):
    """Validated config for constructing an InstroMavStream from JSON."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    instrument: Literal["InstroMavStream"] = "InstroMavStream"
    device: DeviceInfo
    connection: ConnectionConfig
    channels: list[str] | Literal["all"] = Field(
        default="all", description="Channels to stream ('ATTITUDE.roll') or whole messages ('ATTITUDE'), or 'all'."
    )
    timing: TimingConfig | None = None
    publishers: list[PublisherConfigType] = Field(default_factory=list)
