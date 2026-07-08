import pytest

from instro.lib import Command, Measurement
from instro.lib.publishers import Publisher
from instro.lib.publishers.publisher import SharedPublisher


class DummyPublisher(Publisher):
    def __init__(self):
        self.closed = False

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_cloned_and_closed_independently():
    dummy = DummyPublisher()
    publisher = SharedPublisher(dummy)
    cloned = publisher.clone()

    assert not dummy.closed

    publisher.close()
    assert not dummy.closed

    cloned.close()
    assert dummy.closed

def test_cloning_closed_publisher_raises_error():
    dummy = DummyPublisher()
    publisher = SharedPublisher(dummy)

    publisher.close()

    with pytest.raises(RuntimeError):
        publisher.clone()

def test_idempotent_close():
    dummy = DummyPublisher()
    publisher = SharedPublisher(dummy)
    publisher.close()
    publisher.close()
    assert dummy.closed
