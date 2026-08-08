import sys
import threading

from instro.lib import Command, Measurement
from instro.lib.publishers import BasicBufferedPublisher, FilePublisher, Publisher


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
    assert publisher.buffer == []


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


def test_threshold_triggered_flush_clears_buffer_before_close():
    # The auto-flush inside publish() (buffer_size reached) has its own buffer.clear(),
    # separate from the one in close(). Cover that path too: a full batch flushed by
    # publish(), a trailing item left for close() to drain, and no duplication either way.
    sink = RecordingPublisher()
    measurements = [Measurement(channel_data={"voltage": [float(i)]}, timestamps=[i]) for i in range(4)]
    publisher = BasicBufferedPublisher(sink, buffer_size=3)

    for m in measurements[:3]:
        publisher.publish(m)  # 3rd publish() hits buffer_size and auto-flushes
    assert publisher.buffer == []  # auto-flush clears the buffer on its own, before any close()

    publisher.publish(measurements[3])  # buffered again, below threshold
    publisher.close()  # drains the trailing item
    publisher.close()  # must not replay anything

    assert sink.published == measurements
    assert publisher.buffer == []
    assert sink.close_count == 1


def test_repeated_close_never_touches_underlying_publisher_twice():
    sink = StrictRecordingPublisher()
    measurement = Measurement(channel_data={"voltage": [1.0]}, timestamps=[100])
    publisher = BasicBufferedPublisher(sink, buffer_size=1000)

    publisher.publish(measurement)
    publisher.close()
    publisher.close()
    publisher.close()

    assert sink.published == [measurement]
    assert sink.close_count == 1


def test_publish_after_close_never_reaches_underlying_publisher():
    # RecordingPublisher can't tell "never called after close" from "called and happened
    # to look fine"; StrictRecordingPublisher raises if publish()/close() lands on it once
    # closed, so this proves the dropped writes never reach the wrapped publisher at all.
    sink = StrictRecordingPublisher()
    m1 = Measurement(channel_data={"voltage": [1.0]}, timestamps=[100])
    late = [Measurement(channel_data={"voltage": [float(i)]}, timestamps=[i]) for i in range(200, 205)]
    publisher = BasicBufferedPublisher(sink, buffer_size=1000)

    publisher.publish(m1)
    publisher.close()
    for m in late:
        publisher.publish(m)
    publisher.close()

    assert sink.published == [m1]
    assert sink.close_count == 1
    # Late publishes must be actively dropped, not merely stuck unflushed in the buffer.
    assert publisher.buffer == []


def test_double_close_does_not_replay_buffer_into_closed_file(tmp_path):
    # Regression test for #232: close() used to leave the drained buffer in place, so a
    # second close() (e.g. an explicit close() followed by __exit__) replayed it into the
    # already-closed file writer, raising "ValueError: I/O operation on closed file" for
    # handle-based writers like jsonl/avro.
    measurement = Measurement(channel_data={"voltage": [1.0]}, timestamps=[100])
    publisher = BasicBufferedPublisher(
        FilePublisher(tmp_path, format="jsonl", custom_file_name="capture"), buffer_size=1000
    )

    publisher.publish(measurement)  # buffered, below threshold
    publisher.close()  # drains buffer, closes the file
    publisher.close()  # must be a no-op, not a replay into the closed file

    lines = (tmp_path / "capture.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert publisher.buffer == []
