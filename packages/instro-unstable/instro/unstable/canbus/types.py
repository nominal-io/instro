"""JSON config for `CANInterface`: which adapter to open, which DBC describes the bus, and which messages to use."""

# No `from __future__ import annotations`: Sphinx resolves a string-typed `timing: TimingConfig`
# ambiguously (the repo has several TimingConfig classes), which fails the strict docs build.

import difflib
import json
from pathlib import Path
from typing import Any, Literal

import cantools
from cantools.database.can import Database, Message
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from instro.lib.config import TimingConfig
from instro.lib.types import DeviceInfo
from instro.unstable.transports.can import CanConfig, CanTransport


class CANConnectionInfo(BaseModel):
    """The CAN adapter to open. Fields map one-to-one onto the transport's `CanConfig`."""

    model_config = ConfigDict(extra="forbid")

    interface: str
    channel: str | int
    bitrate: int = 500_000
    bus_kwargs: dict[str, Any] = Field(default_factory=dict)

    def build(self) -> CanTransport:
        """Build a private `CanTransport` for this adapter."""
        return CanTransport(
            CanConfig(channel=self.channel, interface=self.interface, bitrate=self.bitrate, bus_kwargs=self.bus_kwargs)
        )


class CANConfig(BaseModel):
    """Complete `CANInterface` configuration. Load from JSON via ``CANConfig.from_json(path)``.

    Signal definitions (bit layout, scaling, units) come only from the DBC file, which is
    parsed once here so a bad file or a misspelled message name fails before any hardware
    is touched.

    Example:
        >>> config = CANConfig(
        ...     device=DeviceInfo(name="vehicle_bus"),
        ...     connection=CANConnectionInfo(interface="gs_usb", channel=0),
        ...     dbc=Path("vehicle.dbc"),
        ...     rx_messages=["BMS_Status"],
        ...     tx_messages=["Motor_Command"],
        ... )  # doctest: +SKIP
    """

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    protocol: Literal["can"] = "can"
    device: DeviceInfo
    timing: TimingConfig | None = None
    connection: CANConnectionInfo | None = None
    dbc: Path = Field(description="DBC file. Relative paths resolve against the JSON file when loaded with from_json.")
    rx_messages: list[str] | None = Field(default=None, description="Messages to receive. Omit for every DBC message.")
    tx_messages: list[str] = Field(default_factory=list, description="Messages this interface may send.")

    _database: Database = PrivateAttr()

    def model_post_init(self, __context: Any) -> None:
        """Load the DBC and check every configured message name against it."""
        database = cantools.database.load_file(self.dbc)
        if not isinstance(database, Database):
            raise ValueError(f"{self.dbc} is not a CAN database (got {type(database).__name__}).")
        self._database = database

        for name in [*(self.rx_messages or []), *self.tx_messages]:
            self.get_message(name)

        for message in self.tx_dbc_messages:
            if message.is_multiplexed():
                raise ValueError(
                    f"tx_messages: '{message.name}' is multiplexed; writing multiplexed messages is not supported."
                )

    @classmethod
    def from_json(cls, path: Path | str) -> "CANConfig":
        """Load and validate a config from a JSON file, resolving ``dbc`` relative to that file."""
        path = Path(path)
        raw = json.loads(path.read_text())
        if "dbc" in raw:
            # An absolute dbc path survives the join unchanged.
            raw["dbc"] = path.parent / raw["dbc"]
        return cls.model_validate(raw)

    @property
    def database(self) -> Database:
        """The parsed DBC."""
        return self._database

    @property
    def rx_dbc_messages(self) -> list[Message]:
        """DBC messages to receive: ``rx_messages``, or every message in the DBC when that is omitted."""
        if self.rx_messages is None:
            return list(self._database.messages)
        return [self.get_message(name) for name in self.rx_messages]

    @property
    def tx_dbc_messages(self) -> list[Message]:
        """DBC messages this interface may send."""
        return [self.get_message(name) for name in self.tx_messages]

    def get_message(self, name: str) -> Message:
        """Return the DBC message called ``name``. Raises ``KeyError`` naming the closest match if there is none."""
        try:
            return self._database.get_message_by_name(name)
        except KeyError:
            known = [message.name for message in self._database.messages]
            hint = difflib.get_close_matches(name, known, n=1)
            suggestion = f" Did you mean '{hint[0]}'?" if hint else ""
            raise KeyError(f"Message '{name}' is not in {self.dbc.name}.{suggestion} Available: {known}") from None
