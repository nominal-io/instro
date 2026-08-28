"""Unit tests for the in-memory channel buffer publishers."""

import time

from instro.lib.publishers.channel_buffer import NumpyInMemoryPublisher
from instro.lib.types import Measurement


def test_numpy_publisher_stores_and_reads_back_a_numeric_channel():
    publisher = NumpyInMemoryPublisher(maxlen=10)
    publisher.publish(Measurement(channel_data={"ut.v": [1.0]}, timestamps=[time.time_ns()]))

    assert publisher.get("ut.v").latest == 1.0


def test_numpy_publisher_stores_and_reads_back_a_string_channel():
    """A string-valued Measurement (e.g. a categorical read) must not crash the float32 ring."""
    publisher = NumpyInMemoryPublisher(maxlen=10)
    publisher.publish(Measurement(channel_data={"ut.ch1.coupling": ["DC"]}, timestamps=[time.time_ns()]))

    assert publisher.get("ut.ch1.coupling").latest == "DC"


def test_numpy_publisher_handles_a_numeric_and_a_string_channel_from_one_measurement():
    publisher = NumpyInMemoryPublisher(maxlen=10)
    ts = time.time_ns()
    publisher.publish(Measurement(channel_data={"ut.numeric": [1.0], "ut.categorical": ["DC"]}, timestamps=[ts]))

    assert publisher.get("ut.numeric").latest == 1.0
    assert publisher.get("ut.categorical").latest == "DC"
    assert set(publisher.channel_names) == {"ut.numeric", "ut.categorical"}


def test_numpy_publisher_size_bytes_does_not_crash_with_a_string_channel():
    publisher = NumpyInMemoryPublisher(maxlen=10)
    publisher.publish(Measurement(channel_data={"ut.v": [1.0]}, timestamps=[time.time_ns()]))
    publisher.publish(Measurement(channel_data={"ut.ch1.coupling": ["DC"]}, timestamps=[time.time_ns()]))

    assert publisher.size_bytes > 0
