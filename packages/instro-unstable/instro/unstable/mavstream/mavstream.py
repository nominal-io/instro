"""MAVLink stream instrument: reads a live MAVLink stream and publishes each field as a channel."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pymavlink import mavutil

from instro.lib.config import build_publisher, load_config
from instro.lib.instrument import Instrument, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Measurement
from instro.unstable.mavstream.config import MavStreamConfig
from instro.unstable.mavstream.parsing import get_channel_values, read_messages

logger = logging.getLogger(__name__)


class InstroMavStream(Instrument):
    """Reads a MAVLink stream over UDP or TCP and publishes each message field as ``<name>.<MESSAGE>.<field>``."""

    # Seconds between drains of the socket; short so that the socket buffer does not fill between drains
    DEFAULT_POLL_INTERVAL = 0.01

    def __init__(
        self,
        name: str | None = None,
        address: str | None = None,
        publishers: list[Publisher] | None = None,
        config: MavStreamConfig | dict | Path | str | None = None,
        autostart: bool = False,
        **kwargs,
    ):
        """Initialize an InstroMavStream.

        Provide either ``config``, or ``name`` and ``address`` together.

        Args:
            name: Channel-name prefix for published data. Falls back to
                ``config.device.name`` when ``config`` is given.
            address: pymavlink connection string. ``udp:<host>:<port>`` listens for a
                UDP stream, ``udpout:<host>:<port>`` sends to a UDP endpoint first, and
                ``tcp:<host>:<port>`` connects to a TCP server::

                    mav = InstroMavStream("drone", address="udp:0.0.0.0:14550", dataset_rid="<rid>")
                    mav.add_channel("ATTITUDE")  # every field of one message
                    mav.add_channel("GPS_RAW_INT.lat")  # one field
                    mav.start()
                    ...
                    mav.close()

                With no ``add_channel`` call, the instrument streams every channel.
            publishers: Publishers that receive emitted Measurement data.
                Combined with any publishers declared in ``config``.
            config: A ``MavStreamConfig``, a dict, or a path to a JSON config file.
            autostart: When True, open the connection and start streaming.
            **kwargs: Default tags applied to every emitted Measurement.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher
                (uses the on-disk 'default' Nominal credential).
        """
        # TODO: accept a custom MAVLink XML definition file and load it before connecting
        # Resolve the config into the same values the direct arguments give
        poll_interval = self.DEFAULT_POLL_INTERVAL
        channels: set[str] | None = None
        config_publishers: list[Publisher] = []
        if config is not None:
            if address is not None:
                raise ValueError("InstroMavStream(config=...) cannot be combined with address.")
            resolved = load_config(config, MavStreamConfig)
            name = name or resolved.device.name
            address = resolved.connection.address
            if resolved.channels != "all":
                channels = set(resolved.channels)
            if resolved.timing is not None:
                poll_interval = resolved.timing.poll_interval
            config_publishers = [build_publisher(p) for p in resolved.publishers]
            publishers = [*(publishers or []), *config_publishers] or None
        elif name is None or address is None:
            raise ValueError("InstroMavStream requires either config=..., or name and address together.")

        super().__init__(name, publishers=publishers, **kwargs)

        # Store the connection info; open() makes the connection
        self._address = address
        # None streams every channel
        self._channels = channels
        self._conn: Any = None

        # Drain the stream from the background daemon
        self.define_background_daemon(self._read_stream)
        self.background_interval = poll_interval

        if autostart:
            try:
                self.start()
            except Exception:
                for publisher in config_publishers:
                    publisher.close()
                raise

    def add_channel(self, channel: str) -> None:
        """Add a channel to stream. The first call narrows the stream from every channel to the added ones.

        Args:
            channel: A channel name (``"ATTITUDE.roll"``) or a message name (``"ATTITUDE"``)
                for every field of that message. A message with an instance field
                publishes ``"<MESSAGE>.<field>[<instance>]"``; give the name without
                ``[<instance>]`` for all instances.
        """
        if self._channels is None:
            self._channels = set()
        self._channels.add(channel)

    def open(self) -> None:
        """Connect to the MAVLink stream."""
        logger.info("Opening MAVLink stream '%s' at '%s'", self.name, self._address)
        self._conn = mavutil.mavlink_connection(self._address)

    def close(self) -> None:
        """Stop streaming, close the publishers, and close the connection."""
        try:
            super().close()
        finally:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def start(self) -> None:
        """Open the connection if it is not open, then start streaming in the background."""
        if self._conn is None:
            self.open()
        super().start()

    @publish_measurement
    def _read_stream(self) -> list[Measurement]:
        """Drain every pending message and package the selected channels."""
        measurements = []
        for message in read_messages(self._conn):
            for channel, timestamp, value in get_channel_values(message):
                # A selection matches the full name, the name without its instance, or the message name
                if self._channels is None or self._channels & {channel, channel.split("[")[0], channel.split(".")[0]}:
                    measurements.append(self._package_measurement(channel, value, timestamp))
        return measurements
