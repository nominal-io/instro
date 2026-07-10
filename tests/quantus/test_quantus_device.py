"""QuantusDevice unit tests against a stubbed `quantus` wheel."""

import sys
import types
from unittest.mock import Mock

import pytest


class FakeReader:
    def __init__(self, events):
        self._events = list(events)
        self.closed = False

    def next_event(self, timeout_ms=1000):
        return self._events.pop(0) if self._events else None

    def health(self):
        return {"packets": 1, "gaps": 0, "missing_packets": 0, "epoch_restarts": 0, "buffer_level": 0.0}

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, config):
        self.config = config
        self.events = []
        self.write_settings = Mock(return_value=True)
        self.auto_zero = Mock()
        self.bridge_balance = Mock()
        self.put_can_message_list = Mock()
        self.can_transmit = Mock()

    def reconcile(self):
        return {
            "version": "Q2.4.15",
            "restart_required": True,
            "side_effects": None,
            "master_sampling_rate_hz": 131072.0,
            "modules": [
                {"name": "MIC42X7", "item_id": 3, "requested_hz": 100.0, "achieved_hz": 512.0, "divisor": 256.0}
            ],
            "channels": [
                {"alias": "mic", "item_id": 4, "mode": "Microphone Input", "streaming": True, "sample_rate_hz": 512.0},
                {"alias": "shaft", "item_id": 9, "mode": "Enabled", "streaming": True, "sample_rate_hz": None},
            ],
        }

    def open_stream(self):
        return FakeReader(self.events)


class CapturePublisher:
    def __init__(self):
        self.items = []

    def publish(self, data, **kwargs):
        self.items.append(data)

    def close(self):
        pass


@pytest.fixture()
def fake_quantus(monkeypatch):
    module = types.ModuleType("quantus")
    module.QuantusClient = FakeClient
    monkeypatch.setitem(sys.modules, "quantus", module)
    return module


def make_device(publisher):
    from instro.quantus import QuantusDevice

    device = QuantusDevice(config={"connection": {"host": "x"}}, name="q", publishers=[publisher])
    device.open()
    return device


def test_name_falls_back_to_config_device_name(fake_quantus):
    from instro.quantus import QuantusDevice

    device = QuantusDevice(config={"connection": {"host": "x"}, "device": {"name": "rig7"}})
    assert device.name == "rig7"
    named = QuantusDevice(config={"connection": {"host": "x"}, "device": {"name": "rig7"}}, name="override")
    assert named.name == "override"
    bare = QuantusDevice(config={"connection": {"host": "x"}})
    assert bare.name == "quantus"


def test_connection_override_merges_into_config(fake_quantus):
    import json

    from instro.quantus import QuantusDevice

    device = QuantusDevice(
        config={"connection": {"host": "old", "rest_port": 9000}},
        connection={"host": "10.0.0.202"},
    )
    device.open()
    sent = json.loads(device._client.config)
    assert sent["connection"] == {"host": "10.0.0.202", "rest_port": 9000}


def test_missing_connection_is_a_value_error(fake_quantus):
    from instro.quantus import QuantusDevice

    with pytest.raises(ValueError, match="connection"):
        QuantusDevice(config={"modules": []})
    # An override alone satisfies the requirement.
    device = QuantusDevice(config={"modules": []}, connection={"host": "10.0.0.202"})
    assert device.name == "quantus"


def test_autostart_opens_reconciles_and_starts(fake_quantus):
    from instro.quantus import QuantusDevice

    device = QuantusDevice(
        config={"connection": {"host": "x"}},
        publishers=[CapturePublisher()],
        autostart=True,
    )
    try:
        assert device._is_open
        assert device._report is not None
        assert device._reader is not None
        assert device._background_thread is not None and device._background_thread.is_alive()
    finally:
        device.close()
    assert not device._background_thread.is_alive()


def test_reconcile_builds_alias_maps(fake_quantus):
    device = make_device(CapturePublisher())
    report = device.reconcile()
    assert report["restart_required"] is True
    assert device._item_by_alias == {"mic": 4, "shaft": 9}


def test_analog_event_becomes_measurement_with_hw_timestamps(fake_quantus):
    publisher = CapturePublisher()
    device = make_device(publisher)
    device.reconcile()
    device._client.events = [
        {"type": "analog", "channel_id": 4, "timestamp_ns": 0, "integrity": 0, "min": 0.0, "max": 1.0,
         "samples": [0.5, 0.6, 0.7]},
    ]
    device.start(background=False)
    device._reader = device._client.open_stream()
    measurement = device._pump()

    assert list(measurement.channel_data) == ["q.mic"]
    assert measurement.channel_data["q.mic"] == [0.5, 0.6, 0.7]
    # 512 Hz -> ~1.95 ms between samples.
    deltas = [b - a for a, b in zip(measurement.timestamps, measurement.timestamps[1:])]
    assert all(abs(d - round(1e9 / 512)) < 2 for d in deltas)
    assert publisher.items == [measurement]


def test_tacho_edges_become_rpm(fake_quantus):
    device = make_device(CapturePublisher())
    device.reconcile()
    device._client.events = [
        {"type": "tacho", "channel_id": 9, "events_ms": [0.0, 20.0, 40.0]},
    ]
    device.start(background=False)
    device._reader = device._client.open_stream()
    measurement = device._pump()

    # 20 ms/rev = 3000 rpm; first edge has no predecessor.
    assert measurement.channel_data["q.shaft"] == pytest.approx([3000.0, 3000.0])


def test_gap_publishes_missing_packet_count(fake_quantus):
    publisher = CapturePublisher()
    device = make_device(publisher)
    device.reconcile()
    device._client.events = [{"type": "gap", "missing": 7}]
    device.start(background=False)
    device._reader = device._client.open_stream()
    measurement = device._pump()
    assert measurement.channel_data["q.stream.missing_packets"] == [7.0]


def test_write_paths_address_channels_by_alias(fake_quantus):
    device = make_device(CapturePublisher())
    device.reconcile()
    assert device.write_settings("mic", {"Voltage Range": "1.2 V"}) is True
    device._client.write_settings.assert_called_once_with(4, {"Voltage Range": "1.2 V"})
    device.auto_zero()
    device._client.auto_zero.assert_called_once_with(None)
    device.can_transmit("shaft", [{"Id": 1}])
    device._client.can_transmit.assert_called_once_with(9)
    with pytest.raises(KeyError):
        device.write_settings("nope", {})


def test_stop_closes_reader(fake_quantus):
    device = make_device(CapturePublisher())
    device.reconcile()
    device.start(background=False)
    device._reader = device._client.open_stream()
    reader = device._reader
    device.stop()
    assert reader.closed
