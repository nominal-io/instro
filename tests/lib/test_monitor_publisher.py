"""Tests for MonitorPublisher, the monitor wire protocol, and MonitorServer/MonitorState."""

import math
import socket
import time
from typing import Callable

import pytest

from instro.lib.consumers.monitor import MonitorServer, MonitorState, decode, encode
from instro.lib.publishers import MonitorPublisher
from instro.lib.types import Command, Measurement

WAIT_TIMEOUT = 5.0


def _wait_for(predicate: Callable[[], bool], timeout: float = WAIT_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    state = MonitorState()
    server = MonitorServer(state, host="127.0.0.1", port=0)
    server.start()
    yield server
    server.shutdown()


def test_encode_decode_roundtrip_preserves_kind_values_and_nan():
    measurement = Measurement(
        channel_data={"psu.ch1.voltage": [1.5, math.nan], "psu.ch1.mode": ["CV", "CC"]},
        timestamps=[10, 20],
        tags={"rig": "a"},
    )
    command = Command(channel_data={"psu.ch1.voltage.cmd": 3.3}, timestamp=30)

    decoded_measurement = decode(encode(measurement))
    decoded_command = decode(encode(command))

    assert isinstance(decoded_measurement, Measurement)
    assert decoded_measurement.channel_data["psu.ch1.voltage"][0] == 1.5
    assert math.isnan(decoded_measurement.channel_data["psu.ch1.voltage"][1])
    assert decoded_measurement.channel_data["psu.ch1.mode"] == ["CV", "CC"]
    assert decoded_measurement.timestamps == [10, 20]
    assert decoded_measurement.tags == {"rig": "a"}
    assert isinstance(decoded_command, Command)
    assert decoded_command.channel_data == {"psu.ch1.voltage.cmd": 3.3}
    assert decoded_command.timestamp == 30
    assert decoded_command.tags is None


def test_decode_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown monitor frame kind"):
        decode('{"kind": "bogus"}')


def test_publisher_streams_to_server_and_disconnect_marks_channels_gone(server):
    publisher = MonitorPublisher(port=server.port)
    publisher.publish(Measurement(channel_data={"psu.ch1.voltage": [1.0, 2.0]}, timestamps=[1, 2]))
    publisher.publish(Command(channel_data={"psu.ch1.voltage.cmd": 5.0}, timestamp=3))

    _wait_for(lambda: len(server.state.snapshot()) == 2)
    by_channel = {s.channel: s for s in server.state.snapshot()}
    voltage = by_channel["psu.ch1.voltage"]
    assert (voltage.kind, voltage.value, voltage.timestamp, voltage.count) == ("measurement", 2.0, 2, 2)
    assert voltage.instrument == "psu"
    assert voltage.connected
    assert by_channel["psu.ch1.voltage.cmd"].kind == "command"
    assert list(server.state.sources().values()) == [True]

    publisher.close()

    _wait_for(lambda: not any(s.connected for s in server.state.snapshot()))
    assert list(server.state.sources().values()) == [False]


def test_publish_without_listener_never_raises_and_close_returns_promptly():
    publisher = MonitorPublisher(port=_free_port(), reconnect_interval=0.05)
    publisher.publish(Measurement(channel_data={"x.a": [1.0]}, timestamps=[1]))

    start = time.monotonic()
    publisher.close()

    assert time.monotonic() - start < 2.0


def test_full_queue_drops_oldest_frame():
    publisher = MonitorPublisher(port=_free_port(), max_queue_size=2, reconnect_interval=60)
    frames = [Command(channel_data={"x.a.cmd": float(i)}, timestamp=i) for i in range(3)]
    try:
        for frame in frames:
            publisher.publish(frame)
        assert publisher.dropped == 1
        assert list(publisher._queue.queue) == frames[1:]
    finally:
        publisher._stop_event.set()


def test_server_skips_malformed_lines_and_keeps_reading(server):
    with socket.create_connection(("127.0.0.1", server.port)) as sock:
        sock.sendall(b"not json\n" + encode(Command(channel_data={"dmm.reading.cmd": 1.0}, timestamp=1)))
        _wait_for(lambda: len(server.state.snapshot()) == 1)

    assert server.state.snapshot()[0].channel == "dmm.reading.cmd"


def test_state_clear_forgets_channels_and_dead_sources():
    state = MonitorState()
    state.source_connected("live")
    state.source_connected("dead")
    state.ingest("live", Command(channel_data={"a.x.cmd": 1.0}, timestamp=1))
    state.source_disconnected("dead")

    state.clear()

    assert state.snapshot() == []
    assert state.sources() == {"live": True}
