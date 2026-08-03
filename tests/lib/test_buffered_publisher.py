import sys
import threading

from instro.lib import Command, Measurement
from instro.lib.publishers import BasicBufferedPublisher, Publisher


class RecordingPublisher(Publisher):
    def __init__(self):
        self.close_count = 0
        self.published: list[Measurement | Command] = []

    def publish(self, data: Measurement | Command, **kwargs: object) -> None:
        self.published.append(data)

    def close(self) -> None:
        self.close_count += 1


class StrictRecordingPublisher(Publisher):
    """Raises if used after close, like a real closed file/socket would.

    Its own writes are serialized with a lock so the test isolates races in
    ``BufferedPublisher`` itself rather than in this test double.
    """

    def __init__(self):
        self.close_count = 0
        self.closed = False
        self.published: list[Measurement | Command] = []
        self._lock = threading.Lock()

    def publish(self, data: Measurement | Command, **kwargs: object) -> None:
        with self._lock:
            if self.closed:
                raise RuntimeError("publish() called after close()")
            self.published.append(data)

    def close(self) -> None:
        with self._lock:
            if self.closed:
                raise RuntimeError("close() called twice")
            self.closed = True
            self.close_count += 1


def test_close_flushes_remaining_buffer_and_closes_underlying():
    sink = RecordingPublisher()
    measurement = Measurement(channel_data={"voltage": [1.0]}, timestamps=[100])
    publisher = BasicBufferedPublisher(sink, buffer_size=1000)

    publisher.publish(measurement)
    publisher.close()

    assert sink.published == [measurement]
    assert sink.close_count == 1


def test_repeated_close_is_idempotent():
    sink = RecordingPublisher()
    measurement = Measurement(channel_data={"voltage": [1.0]}, timestamps=[100])
    publisher = BasicBufferedPublisher(sink, buffer_size=1000)

    publisher.publish(measurement)
    publisher.close()
    publisher.close()

    assert sink.published == [measurement]
    assert sink.close_count == 1


def test_publish_after_close_is_dropped():
    sink = RecordingPublisher()
    m1 = Measurement(channel_data={"voltage": [1.0]}, timestamps=[100])
    m2 = Measurement(channel_data={"voltage": [2.0]}, timestamps=[200])
    publisher = BasicBufferedPublisher(sink, buffer_size=1000)

    publisher.publish(m1)
    publisher.close()
    publisher.publish(m2)
    publisher.close()

    assert sink.published == [m1]
    assert sink.close_count == 1


def test_concurrent_publish_is_thread_safe():
    # A smaller switch interval forces frequent thread interleaving so a race in the
    # buffer-size-triggered flush reproduces reliably instead of only on unlucky runs.
    thread_count = 8
    iterations = 500
    original_switch_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        sink = StrictRecordingPublisher()
        publisher = BasicBufferedPublisher(sink, buffer_size=8)

        def publish_loop(offset: int):
            for i in range(iterations):
                publisher.publish(Measurement(channel_data={"voltage": [float(offset + i)]}, timestamps=[offset + i]))

        threads = [threading.Thread(target=publish_loop, args=(t * iterations,)) for t in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        publisher.close()
    finally:
        sys.setswitchinterval(original_switch_interval)

    seen_timestamps = [m.timestamps[0] for m in sink.published if isinstance(m, Measurement)]
    assert publisher.buffer == []
    # Every measurement reached the sink exactly once: no data lost or double-processed
    # by concurrent buffer-size-triggered flushes racing each other.
    assert len(seen_timestamps) == len(set(seen_timestamps)) == thread_count * iterations


def test_concurrent_publish_and_close_is_thread_safe():
    # close() races the in-flight publish loops on purpose here, so some publishes are
    # expected to be dropped as "closed" - the invariant is no crash/double-close/double-add,
    # not that every message gets through.
    thread_count = 8
    iterations = 300
    original_switch_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        sink = StrictRecordingPublisher()
        publisher = BasicBufferedPublisher(sink, buffer_size=8)
        errors: list[BaseException] = []

        def publish_loop(offset: int):
            for i in range(iterations):
                try:
                    publisher.publish(
                        Measurement(channel_data={"voltage": [float(offset + i)]}, timestamps=[offset + i])
                    )
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

        threads = [threading.Thread(target=publish_loop, args=(t * iterations,)) for t in range(thread_count)]
        for thread in threads:
            thread.start()
        publisher.close()
        for thread in threads:
            thread.join()
        publisher.close()
    finally:
        sys.setswitchinterval(original_switch_interval)

    seen_timestamps = [m.timestamps[0] for m in sink.published if isinstance(m, Measurement)]
    assert errors == []
    assert sink.close_count == 1
    assert publisher.buffer == []
    # Whatever did reach the sink got there exactly once: no double-processing.
    assert len(seen_timestamps) == len(set(seen_timestamps))
