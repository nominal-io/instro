import logging
from unittest.mock import MagicMock

from instro.lib import Instrument
from instro.lib.publishers.channel_buffer import DequeInMemoryPublisher
from instro.lib.types import BackgroundDaemonConfig, Command, Measurement


def _measurement(value: float, timestamp: int) -> Measurement:
    return Measurement(channel_data={"ut.v": [value]}, timestamps=[timestamp])


def test_a_failing_publisher_is_counted_logged_once_and_skipped(caplog):
    failing = MagicMock()
    failing.publish.side_effect = RuntimeError("backend down")
    healthy = DequeInMemoryPublisher(maxlen=5)
    instrument = Instrument(name="ut", publishers=[failing, healthy])

    with caplog.at_level(logging.WARNING, logger="instro.lib.stats"):
        instrument.publish(_measurement(1.0, 10))
        instrument.publish(_measurement(2.0, 20))

    assert healthy.latest() == {"ut.v": (20, 2.0)}
    failing_stats, healthy_stats = instrument.publisher_stats
    assert (failing_stats.sent, failing_stats.errors) == (0, 2)
    assert failing_stats.last_error == "RuntimeError: backend down"
    assert (healthy_stats.sent, healthy_stats.errors) == (2, 0)
    assert len(caplog.records) == 1
    assert "2 publish errors" in (instrument._repr_html_() or "")


def test_channel_stats_cover_commands_without_start():
    instrument = Instrument(name="ut")

    instrument.publish(Command(channel_data={"ut.v.cmd": 3.3}, timestamp=10))
    instrument.publish(Measurement(channel_data={"ut.v": [1.0, 2.0]}, timestamps=[20, 30]))

    by_name = {state.channel: state for state in instrument.channel_stats}
    assert (by_name["ut.v.cmd"].kind, by_name["ut.v.cmd"].value, by_name["ut.v.cmd"].count) == ("command", 3.3, 1)
    assert (by_name["ut.v"].value, by_name["ut.v"].timestamp, by_name["ut.v"].count) == (2.0, 30, 2)


def test_restarting_does_not_keep_counters_for_dropped_buffers():
    instrument = Instrument(name="ut", background_config=BackgroundDaemonConfig(interval=0.01))
    for _ in range(3):
        instrument.start()
        instrument.get_channel("loop_time", wait_for_new_samples=True)
        instrument.stop()

    assert instrument._publisher_counters == {}
