"""Publisher that streams Measurement/Command data to Nominal Connect."""

from __future__ import annotations

import logging
import typing as _typing

if _typing.TYPE_CHECKING:
    from connect_python.client import Client  # type: ignore

from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)


class NominalConnectPublisher:
    """Publish Measurement/Command data to a Nominal Connect stream.

    String values are silently dropped — Connect does not accept strings.
    """

    DEFAULT_STREAM_SOURCE = "nominal_instrumentation"

    def __init__(self, client: Client, stream_id: str):
        self._client = client
        self._stream_id = stream_id
        self._source = self.DEFAULT_STREAM_SOURCE
        self._set_client_source()

    def _set_client_source(self) -> None:
        """Tag the connect-python client with our source via its ``_set_source()`` hook (no-op if missing)."""
        set_source = getattr(self._client, "_set_source", None)
        if callable(set_source):
            try:
                set_source(self._source)
                return
            except Exception as e:
                logger.warning(f"Failed to set connect stream source via _set_source(): {e}")
                return

        logger.warning(
            f"Connect client does not expose _set_source(); stream source could not be set to '{self._source}'."
        )

    def publish(self, data: Measurement | Command, **kwargs):
        """Publish ``data`` to Nominal Connect, one stream_batch per channel. Strings are skipped."""
        if isinstance(next(iter(data.channel_data.values())), str):
            # Connect doesn't support strings
            return
        elif isinstance(data, Measurement):
            self.__publish_measurement(data)
        elif isinstance(data, Command):
            self.__publish_command(data)

    def __publish_measurement(self, data):
        for channel in data.channel_data:
            self._client.stream_batch(
                stream_id=self._stream_id,
                timestamps=data.timestamps,
                values=data.channel_data[channel],
                name=channel,
            )

    def __publish_command(self, data):
        for channel in data.channel_data:
            self._client.stream_batch(
                stream_id=self._stream_id,
                timestamps=[data.timestamp],
                values=[data.channel_data[channel]],
                name=channel,
            )

    def close(self):
        """No-op; no resources to release."""
        pass
