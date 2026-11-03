"""Unit tests for Nominal Connect publisher source behavior."""

from unittest.mock import Mock, patch

from instro.lib.publishers import NominalConnectPublisher
from instro.lib.types import Command, Measurement


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


def test_publish_drops_a_string_valued_measurement_channel():
    client = Mock()
    publisher = NominalConnectPublisher(client=client, stream_id="stream-123")
    measurement = Measurement(channel_data={"ut.ch1.coupling": ["DC"]}, timestamps=[1])

    publisher.publish(measurement)

    client.stream_batch.assert_not_called()


def test_publish_sends_the_numeric_channel_and_drops_the_string_channel_from_one_measurement():
    """A single Measurement can carry a float channel and a string channel together; only the string one is dropped, not the whole publish."""
    client = Mock()
    publisher = NominalConnectPublisher(client=client, stream_id="stream-123")
    measurement = Measurement(
        channel_data={"ut.numeric": [1.0], "ut.categorical": ["DC"]},
        timestamps=[1],
    )

    publisher.publish(measurement)

    client.stream_batch.assert_called_once_with(stream_id="stream-123", timestamps=[1], values=[1.0], name="ut.numeric")


def test_publish_drops_an_empty_valued_measurement_channel():
    client = Mock()
    publisher = NominalConnectPublisher(client=client, stream_id="stream-123")
    measurement = Measurement(channel_data={"ut.mode": []}, timestamps=[])

    publisher.publish(measurement)

    client.stream_batch.assert_not_called()


def test_publish_drops_a_string_valued_command_channel():
    client = Mock()
    publisher = NominalConnectPublisher(client=client, stream_id="stream-123")
    command = Command(channel_data={"ut.mode.cmd": "DC"}, timestamp=1)

    publisher.publish(command)

    client.stream_batch.assert_not_called()


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
