import gc

import pytest

from instro.lib import Command, Instrument, Measurement
from instro.lib.publishers import Publisher, SharedPublisher


class RecordingPublisher(Publisher):
    def __init__(self):
        self.close_count = 0
        self.published: list[tuple[Measurement | Command, dict[str, object]]] = []

    def publish(self, data: Measurement | Command, **kwargs: object) -> None:
        self.published.append((data, kwargs))

    def close(self) -> None:
        self.close_count += 1


def test_single_handle_closes_publisher_once():
    sink = RecordingPublisher()
    publisher = SharedPublisher(sink)

    publisher.close()

    assert sink.close_count == 1


def test_cloned_handles_close_publisher_after_final_handle():
    sink = RecordingPublisher()
    publisher = SharedPublisher(sink)
    cloned = publisher.clone()

    publisher.close()
    assert sink.close_count == 0

    cloned.close()
    assert sink.close_count == 1


def test_repeated_close_is_idempotent():
    sink = RecordingPublisher()
    publisher = SharedPublisher(sink)

    publisher.close()
    publisher.close()

    assert sink.close_count == 1


def test_gc_closes_unclosed_handles_once():
    sink = RecordingPublisher()
    publisher = SharedPublisher(sink)
    cloned = publisher.clone()

    del publisher
    gc.collect()
    assert sink.close_count == 0

    del cloned
    gc.collect()
    assert sink.close_count == 1


def test_publish_forwards_payload_and_kwargs():
    sink = RecordingPublisher()
    publisher = SharedPublisher(sink)
    cloned = publisher.clone()
    measurement = Measurement(channel_data={"voltage": [1.0]}, timestamps=[100])
    command = Command(channel_data={"enabled": "true"}, timestamp=200)

    publisher.publish(measurement, source="original")
    cloned.publish(command, source="clone")

    assert sink.published == [
        (measurement, {"source": "original"}),
        (command, {"source": "clone"}),
    ]


def test_publish_after_close_raises_error():
    sink = RecordingPublisher()
    publisher = SharedPublisher(sink)

    publisher.close()

    with pytest.raises(RuntimeError):
        publisher.publish(Measurement(channel_data={"voltage": [1.0]}, timestamps=[100]))


def test_remaining_clone_publishes_after_original_closes():
    sink = RecordingPublisher()
    publisher = SharedPublisher(sink)
    cloned = publisher.clone()
    measurement = Measurement(channel_data={"voltage": [1.0]}, timestamps=[100])

    publisher.close()
    cloned.publish(measurement)

    assert sink.published == [(measurement, {})]


def test_cloning_closed_publisher_raises_error():
    sink = RecordingPublisher()
    publisher = SharedPublisher(sink)

    publisher.close()

    with pytest.raises(RuntimeError):
        publisher.clone()


def test_instrument_close_releases_shared_publisher_after_final_owner():
    sink = RecordingPublisher()
    shared = SharedPublisher(sink)
    primary = Instrument(name="primary", publishers=[shared])
    secondary = Instrument(name="secondary", publishers=[shared.clone()])

    primary.close()
    assert sink.close_count == 0

    secondary.close()
    assert sink.close_count == 1
