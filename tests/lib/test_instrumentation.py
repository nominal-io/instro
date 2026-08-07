import logging
import time
from importlib.metadata import version

from instro.lib import Instrument
from instro.lib.publishers.channel_buffer import DequeInMemoryPublisher
from instro.lib.types import BackgroundDaemonConfig, Measurement
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU


def _make_publishing_instrument() -> Instrument:
    instrument = Instrument(name="ut", background_config=BackgroundDaemonConfig(interval=0.01))
    instrument.add_background_daemon_function(
        lambda: instrument.publish(Measurement(channel_data={"ut.v": [1.0]}, timestamps=[time.time_ns()]))
    )
    return instrument


def test_default_tag_set_base_class():
    current_version = version("instro")
    instrument = Instrument(name="test")
    assert instrument.default_tags == {
        "instro": current_version,
    }


def test_default_tag_set_psu():
    current_version = version("instro")
    instrument = InstroPSU(
        name="test",
        driver=SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET"),
        num_channels=1,
    )
    assert instrument.default_tags == {
        "instro": current_version,
    }


def test_context_manager_calls_open_and_close():
    calls: list[str] = []

    class Probe(Instrument):
        def open(self) -> None:
            calls.append("open")

        def close(self) -> None:
            calls.append("close")
            super().close()

    with Probe(name="probe") as probe:
        assert isinstance(probe, Probe)
        assert calls == ["open"]
    assert calls == ["open", "close"]


def test_get_channel_works_after_restart():
    instrument = _make_publishing_instrument()
    try:
        instrument.start()
        assert instrument.get_channel("ut.v").channel_data == {"ut.v": [1.0]}
        instrument.stop()

        instrument.start()
        assert instrument.get_channel("ut.v").channel_data == {"ut.v": [1.0]}
    finally:
        instrument.stop()


def test_restart_registers_exactly_one_channel_buffer_publisher():
    instrument = _make_publishing_instrument()
    try:
        instrument.start()
        instrument.stop()
        instrument.start()

        buffers = [p for p in instrument.publishers if isinstance(p, DequeInMemoryPublisher)]
        assert len(buffers) == 1
        assert buffers[0] is instrument._channel_buffer
    finally:
        instrument.stop()


def test_stop_removes_channel_buffer_publisher():
    instrument = _make_publishing_instrument()
    instrument.start()

    instrument.stop()

    assert instrument._channel_buffer is None
    assert not any(isinstance(p, DequeInMemoryPublisher) for p in instrument.publishers)


def test_context_manager_closes_on_exception():
    calls: list[str] = []

    class Probe(Instrument):
        def close(self) -> None:
            calls.append("close")
            super().close()

    try:
        with Probe(name="probe"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert calls == ["close"]


def test_channel_names_raises_when_daemon_not_running():
    instrument = Instrument(name="test")
    try:
        _ = instrument.channel_names
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "No channel buffer exists" in str(e)


def test_channel_names_includes_published_channels():
    instrument = _make_publishing_instrument()
    try:
        instrument.start()
        # Wait for the custom channel to be published
        instrument.get_channel("ut.v", wait_for_new_samples=True, timeout=2.0)
        names = instrument.channel_names
        assert "ut.v" in names
        assert "ut.loop_time" in names
    finally:
        instrument.stop()


def test_warns_only_when_requested_interval_is_unachievable(caplog):
    instrument = Instrument(name="ut")
    # Work is kept far from both intervals so scheduling jitter can't flip either assertion.
    instrument.define_background_daemon(lambda: time.sleep(0.005))

    with caplog.at_level(logging.WARNING, logger="instro.lib.instrument"):
        try:
            instrument.background_interval = 0.1
            instrument.start()
            instrument.get_channel("loop_time", length=10, wait_for_new_samples=True, timeout=5.0)
            assert not caplog.records, "warned about an interval the daemon can achieve"

            instrument.background_interval = 0.001
            instrument.get_channel("loop_time", length=10, wait_for_new_samples=True, timeout=5.0)
        finally:
            instrument.stop()

    assert len(caplog.records) == 1
    assert "cannot achieve the requested interval" in caplog.records[0].getMessage()


def test_free_running_interval_never_warns(caplog):
    instrument = Instrument(name="ut")
    instrument.define_background_daemon(lambda: time.sleep(0.005))

    with caplog.at_level(logging.WARNING, logger="instro.lib.instrument"):
        try:
            instrument.background_interval = 0
            instrument.start()
            instrument.get_channel("loop_time", length=10, wait_for_new_samples=True, timeout=5.0)
        finally:
            instrument.stop()

    assert not caplog.records, "warned about a free-running daemon, which has no requested rate"
