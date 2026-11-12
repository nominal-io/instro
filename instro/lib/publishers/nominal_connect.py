"""Publisher that streams Measurement/Command data to Nominal Connect."""

from __future__ import annotations

import logging
import typing as _typing

if _typing.TYPE_CHECKING:
    from connect_python.client import Client  # type: ignore

from instro.lib import _display
from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)


class NominalConnectPublisher:
    """Publish Measurement/Command data to a Nominal Connect stream.

    String values are silently dropped — Connect does not accept strings.
    """

    DEFAULT_STREAM_SOURCE = "nominal_instrumentation"

    def __init__(self, client: Client, stream_id: str):
        self.client = client
        self.stream_id = stream_id
        self.source = self.DEFAULT_STREAM_SOURCE
        self._set_client_source()

    def _set_client_source(self) -> None:
        """Tag the connect-python client with our source via its ``_set_source()`` hook (no-op if missing)."""
        set_source = getattr(self.client, "_set_source", None)
        if callable(set_source):
            try:
                set_source(self.source)
                return
            except Exception as e:
                logger.warning(f"Failed to set connect stream source via _set_source(): {e}")
                return

        logger.warning(
            f"Connect client does not expose _set_source(); stream source could not be set to '{self.source}'."
        )

    def publish(self, data: Measurement | Command, **kwargs):
        """Publish ``data`` to Nominal Connect, one stream_batch per channel. Strings are skipped."""
        if isinstance(data, Measurement):
            self.__publish_measurement(data)
        elif isinstance(data, Command):
            self.__publish_command(data)

    def __publish_measurement(self, data):
        for channel, values in data.channel_data.items():
            if not values or isinstance(values[0], str):
                # Connect doesn't support strings; nothing to publish for an empty channel either
                continue
            self.client.stream_batch(
                stream_id=self.stream_id,
                timestamps=data.timestamps,
                values=values,
                name=channel,
            )

    def __publish_command(self, data):
        for channel, value in data.channel_data.items():
            if isinstance(value, str):
                # Connect doesn't support strings
                continue
            self.client.stream_batch(
                stream_id=self.stream_id,
                timestamps=[data.timestamp],
                values=[value],
                name=channel,
            )

    def close(self):
        """No-op; no resources to release."""
        pass

    def __repr__(self) -> str:
        """Name the stream this publisher writes to."""
        return f"NominalConnectPublisher(stream_id={self.stream_id!r}, source={self.source!r})"

    def _display_summary(self) -> str:
        return f"Nominal Connect stream {_display.code(self.stream_id)}"

    def _repr_html_(self) -> str | None:
        """Show the Connect stream this publisher writes to."""
        return _display.render(
            kind="NominalConnectPublisher",
            title=self.stream_id,
            fallback=repr(self),
            fields=[("Stream", _display.code(self.stream_id)), ("Source", _display.code(self.source))],
        )
