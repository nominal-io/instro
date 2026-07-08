import gc
import threading

import pytest

from instro.lib import Command, Instrument, Measurement
from instro.lib.publishers import Publisher, SharedPublisher

THREAD_TIMEOUT = 1.0


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


def test_explicit_close_then_gc_does_not_close_again():
    sink = RecordingPublisher()
    publisher = SharedPublisher(sink)

    publisher.close()
    del publisher
    gc.collect()

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


def test_concurrent_close_on_same_handle_closes_underlying_once():
    close_started = threading.Event()
    close_can_finish = threading.Event()
    close_calls: list[None] = []
    errors: list[Exception] = []
    second_close_finished = threading.Event()

    class BlockingClosePublisher(Publisher):
        def publish(self, data: Measurement | Command, **kwargs: object) -> None:
            raise AssertionError("publish should not be called")

        def close(self) -> None:
            close_started.set()
            if not close_can_finish.wait(timeout=THREAD_TIMEOUT):
                raise AssertionError("timed out waiting to release close")
            close_calls.append(None)

    publisher = SharedPublisher(BlockingClosePublisher())

    def close_once() -> None:
        try:
            publisher.close()
        except Exception as exc:
            errors.append(exc)

    def close_again() -> None:
        try:
            publisher.close()
        except Exception as exc:
            errors.append(exc)
        finally:
            second_close_finished.set()

    first_thread = threading.Thread(target=close_once)
    second_thread = threading.Thread(target=close_again)

    first_thread.start()
    try:
        assert close_started.wait(timeout=THREAD_TIMEOUT)
        second_thread.start()
        assert not second_close_finished.wait(timeout=0.1)
    finally:
        close_can_finish.set()

    first_thread.join(timeout=THREAD_TIMEOUT)
    second_thread.join(timeout=THREAD_TIMEOUT)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not errors
    assert len(close_calls) == 1


def test_close_waits_for_in_flight_publish_on_same_handle():
    publish_started = threading.Event()
    publish_can_finish = threading.Event()
    close_finished = threading.Event()
    published: list[Measurement | Command] = []
    close_calls: list[None] = []
    errors: list[Exception] = []
    measurement = Measurement(channel_data={"voltage": [1.0]}, timestamps=[100])

    class BlockingPublishPublisher(Publisher):
        def publish(self, data: Measurement | Command, **kwargs: object) -> None:
            publish_started.set()
            if not publish_can_finish.wait(timeout=THREAD_TIMEOUT):
                raise AssertionError("timed out waiting to release publish")
            published.append(data)

        def close(self) -> None:
            close_calls.append(None)

    publisher = SharedPublisher(BlockingPublishPublisher())

    def publish() -> None:
        try:
            publisher.publish(measurement)
        except Exception as exc:
            errors.append(exc)

    def close() -> None:
        try:
            publisher.close()
        except Exception as exc:
            errors.append(exc)
        finally:
            close_finished.set()

    publish_thread = threading.Thread(target=publish)
    close_thread = threading.Thread(target=close)

    publish_thread.start()
    try:
        assert publish_started.wait(timeout=THREAD_TIMEOUT)
        close_thread.start()
        assert not close_finished.wait(timeout=0.1)
    finally:
        publish_can_finish.set()

    publish_thread.join(timeout=THREAD_TIMEOUT)
    close_thread.join(timeout=THREAD_TIMEOUT)

    assert not publish_thread.is_alive()
    assert not close_thread.is_alive()
    assert not errors
    assert published == [measurement]
    assert len(close_calls) == 1
    with pytest.raises(RuntimeError):
        publisher.publish(measurement)


def test_clone_waits_for_concurrent_close_on_same_handle():
    close_started = threading.Event()
    close_can_finish = threading.Event()
    clone_finished = threading.Event()
    close_calls: list[None] = []
    errors: list[Exception] = []
    clone_errors: list[RuntimeError] = []

    class BlockingClosePublisher(Publisher):
        def publish(self, data: Measurement | Command, **kwargs: object) -> None:
            raise AssertionError("publish should not be called")

        def close(self) -> None:
            close_started.set()
            if not close_can_finish.wait(timeout=THREAD_TIMEOUT):
                raise AssertionError("timed out waiting to release close")
            close_calls.append(None)

    publisher = SharedPublisher(BlockingClosePublisher())

    def close() -> None:
        try:
            publisher.close()
        except Exception as exc:
            errors.append(exc)

    def clone() -> None:
        try:
            publisher.clone()
        except RuntimeError as exc:
            clone_errors.append(exc)
        except Exception as exc:
            errors.append(exc)
        finally:
            clone_finished.set()

    close_thread = threading.Thread(target=close)
    clone_thread = threading.Thread(target=clone)

    close_thread.start()
    try:
        assert close_started.wait(timeout=THREAD_TIMEOUT)
        clone_thread.start()
        assert not clone_finished.wait(timeout=0.1)
    finally:
        close_can_finish.set()

    close_thread.join(timeout=THREAD_TIMEOUT)
    clone_thread.join(timeout=THREAD_TIMEOUT)

    assert not close_thread.is_alive()
    assert not clone_thread.is_alive()
    assert not errors
    assert len(close_calls) == 1
    assert len(clone_errors) == 1
    assert str(clone_errors[0]) == "attempted to clone a shared publisher handle that was already closed"


def test_instrument_close_releases_shared_publisher_after_final_owner():
    sink = RecordingPublisher()
    shared = SharedPublisher(sink)
    primary = Instrument(name="primary", publishers=[shared])
    secondary = Instrument(name="secondary", publishers=[shared.clone()])

    primary.close()
    assert sink.close_count == 0

    secondary.close()
    assert sink.close_count == 1
