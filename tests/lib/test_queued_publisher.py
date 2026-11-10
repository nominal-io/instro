from instro.lib import Command, Measurement
from instro.lib.publishers import Publisher, QueuedPublisher


class FailOncePublisher(Publisher):
    """Raises on its first publish, like a transient network or disk error, then records."""

    def __init__(self):
        self.attempts = 0
        self.close_count = 0
        self.published: list[Measurement | Command] = []

    def publish(self, data: Measurement | Command, **kwargs: object) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionError("transient publish failure")
        self.published.append(data)

    def close(self) -> None:
        self.close_count += 1


def test_publish_error_is_logged_and_does_not_stop_the_worker(caplog):
    # The worker used to die on the first error, so every later item sat in the queue unpublished
    # and, once the queue filled, publish() blocked its caller (an instrument method) forever.
    sink = FailOncePublisher()
    publisher = QueuedPublisher(sink, wait_for_queue=True)
    commands = [Command(channel_data={"psu.ch1.voltage.cmd": float(i)}, timestamp=i) for i in range(3)]

    for command in commands:
        publisher.publish(command)
    publisher.close()

    assert sink.published == commands[1:]
    assert sink.close_count == 1
    assert "transient publish failure" in caplog.text
