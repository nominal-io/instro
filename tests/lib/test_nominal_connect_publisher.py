"""Unit tests for Nominal Connect publisher source behavior."""

from unittest.mock import Mock, patch

from instro.lib.publishers import NominalConnectPublisher
from instro.lib.types import Measurement


def test_constructor_uses_instrumentation_source_by_default():
    """Check stream source attachment by verifying default source is set via hidden hook."""
    client = Mock()
    client.stream_batch = Mock()
    client._set_source = Mock()
    publisher = NominalConnectPublisher(client=client, stream_id="stream-123")

    assert publisher._stream_id == "stream-123"
    client._set_source.assert_called_once_with(NominalConnectPublisher.DEFAULT_STREAM_SOURCE)


def test_publish_stream_batch_payload_contains_default_source_using_hidden_set_source():
    """Check stream source attachment by verifying published payload carries instrumentation source."""

    class HiddenSourceClient:
        def __init__(self):
            self._source = "connect_python"
            self.sent = []

        def _set_source(self, source: str):
            self._source = source

        def stream_batch(self, **kwargs):
            self.sent.append({**kwargs, "source": self._source})

    client = HiddenSourceClient()
    publisher = NominalConnectPublisher(client=client, stream_id="stream-123")
    measurement = Measurement(channel_data={"channel_a": [1.0]}, timestamps=[1])

    publisher.publish(measurement)

    assert client.sent[0]["source"] == NominalConnectPublisher.DEFAULT_STREAM_SOURCE


def test_missing_set_source_logs_warning_and_does_not_change_stream_source():
    """Check stream source attachment fallback by verifying source is unchanged when hook is missing."""

    class PublicSourceClient:
        def __init__(self):
            self.source = "connect_python"
            self.sent = []

        def stream_batch(self, **kwargs):
            self.sent.append({**kwargs, "source": self.source})

    client = PublicSourceClient()
    with patch("instro.lib.publishers.nominal_connect.logger.warning") as warn_mock:
        publisher = NominalConnectPublisher(client=client, stream_id="stream-123")
        measurement = Measurement(channel_data={"channel_a": [1.0]}, timestamps=[1])

        publisher.publish(measurement)

    warn_mock.assert_called_once()
    assert client.sent[0]["source"] == "connect_python"


def test_set_source_exception_logs_warning_and_does_not_change_stream_source():
    """Check stream source attachment fallback by verifying source is unchanged when hook errors."""

    class FailingHiddenSourceClient:
        def __init__(self):
            self._source = "connect_python"
            self.sent = []

        def _set_source(self, source: str):
            raise RuntimeError("boom")

        def stream_batch(self, **kwargs):
            self.sent.append({**kwargs, "source": self._source})

    client = FailingHiddenSourceClient()
    with patch("instro.lib.publishers.nominal_connect.logger.warning") as warn_mock:
        publisher = NominalConnectPublisher(client=client, stream_id="stream-123")
        measurement = Measurement(channel_data={"channel_a": [1.0]}, timestamps=[1])

        publisher.publish(measurement)

    warn_mock.assert_called_once()
    assert client.sent[0]["source"] == "connect_python"
