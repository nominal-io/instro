import pytest
from pymeasure.adapters import Adapter
from pymeasure.instruments import Channel, Instrument
from pymeasure.instruments.keysight import KeysightE36312A
from pymeasure.instruments.validators import strict_range
from pymeasure.test import expected_protocol

from instro.lib.types import BackgroundDaemonConfig, Command, Measurement
from instro.pymeasure import ChannelMap, PymeasureInstrument, attach, describe


class RecordingPublisher:
    def __init__(self):
        self.data: list[Measurement | Command] = []
        self.closed = False

    def publish(self, data, **kwargs):
        self.data.append(data)

    def close(self):
        self.closed = True

    def channels(self) -> dict[str, list]:
        out: dict[str, list] = {}
        for item in self.data:
            if isinstance(item, Command):
                for name, value in item.channel_data.items():
                    out.setdefault(name, []).append(value)
            else:
                for name, values in item.channel_data.items():
                    out.setdefault(name, []).extend(values)
        return out


class Probe(Channel):
    temperature = Channel.measurement("TEMP? {ch}", "Measure the probe temperature.")


class Bench(Instrument):
    """Small driver covering the property shapes the wrapper has to handle."""

    level = Instrument.control("LEV?", "LEV %g", "Control the level.", validator=strict_range, values=[0, 10])
    mode = Instrument.control("MODE?", "MODE %s", "Control the mode.", cast=str)
    spectrum = Instrument.measurement("SPEC?", "Measure the three spectral bins.")

    def __init__(self, adapter, **kwargs):
        super().__init__(adapter, "Bench", includeSCPI=False, **kwargs)
        # Added at runtime, so only visible when inspecting an instance.
        self.add_child(Probe, "A")

    def ramp(self, target: float) -> None:
        self.level = target / 2
        self.level = target


def test_set_publishes_command_and_read_publishes_measurement():
    publisher = RecordingPublisher()
    with expected_protocol(
        KeysightE36312A,
        [("VOLT 5, (@1)", None), ("MEAS:CURRent? (@1)", "0.25"), ("OUTPut? (@2)", "1")],
    ) as supply:
        psu = PymeasureInstrument("psu", supply, [publisher], close_device=False)
        supply.ch_1.voltage_setpoint = 5
        assert psu.device.ch_1.current == 0.25
        assert supply.ch_2.output_enabled is True

    command, current, enabled = publisher.data
    assert isinstance(command, Command) and command.channel_data == {"psu.ch1.voltage_setpoint.cmd": 5.0}
    assert isinstance(current, Measurement) and current.channel_data == {"psu.ch1.current": [0.25]}
    assert enabled.channel_data == {"psu.ch2.output_enabled": [1.0]}
    assert command.tags is not None and command.tags["pymeasure-class"] == "KeysightE36312A"


def test_property_access_inside_driver_methods_is_published():
    publisher = RecordingPublisher()
    with expected_protocol(Bench, [("LEV 2", None), ("LEV 4", None)]) as bench:
        attach(bench, "bench", [publisher])
        bench.ramp(4)

    assert publisher.channels() == {"bench.level.cmd": [2.0, 4.0]}


def test_failed_set_publishes_nothing():
    publisher = RecordingPublisher()
    with expected_protocol(Bench, []) as bench:
        attach(bench, "bench", [publisher])
        with pytest.raises(ValueError):
            bench.level = 99

    assert publisher.data == []


def test_string_and_short_list_values():
    publisher = RecordingPublisher()
    with expected_protocol(Bench, [("MODE?", "FAST"), ("SPEC?", "1,2,3")]) as bench:
        attach(bench, "bench", [publisher])
        bench.mode
        bench.spectrum

    assert publisher.channels() == {
        "bench.mode": ["FAST"],
        "bench.spectrum.0": [1.0],
        "bench.spectrum.1": [2.0],
        "bench.spectrum.2": [3.0],
    }


def test_runtime_channels_need_an_instance():
    assert "ch_A.temperature" not in {spec.path for spec in describe(Bench)}
    with expected_protocol(Bench, []) as bench:
        specs = {spec.path: spec for spec in describe(bench)}
    assert specs["ch_A.temperature"].kind == "measurement"
    assert specs["ch_A.temperature"].default_name == "chA.temperature"
    assert specs["level"].kind == "control"


def test_class_inspection_keeps_channels_that_share_a_class():
    paths = {spec.path for spec in describe(KeysightE36312A)}
    assert {"ch_1.voltage_setpoint", "ch_2.voltage_setpoint", "ch_3.voltage_setpoint"} <= paths


def test_channel_map_renames_and_excludes(tmp_path):
    with expected_protocol(Bench, []) as bench:
        generated = ChannelMap.generate(bench)
        path = tmp_path / "bench_map.json"
        generated.save(path, bench)

    edited = ChannelMap.load(path)
    edited.channels["level"] = "setpoint"
    edited.channels["mode"] = None

    publisher = RecordingPublisher()
    with expected_protocol(Bench, [("LEV 1", None), ("MODE?", "SLOW"), ("TEMP? A", "21.5")]) as bench:
        attach(bench, "bench", [publisher], channel_map=edited)
        bench.level = 1
        bench.mode
        bench.ch_A.temperature

    assert publisher.channels() == {"bench.setpoint.cmd": [1.0], "bench.chA.temperature": [21.5]}


def test_notebook_editing_and_preview_separate_commands_from_measurements():
    with expected_protocol(Bench, []) as bench:
        channel_map = ChannelMap.generate(bench).select("level", "spectrum").rename({"level": "setpoint"})
        rows = {row["property"]: row for row in channel_map.preview(bench, "bench")}
        with pytest.raises(KeyError, match="ch_B"):
            channel_map.exclude("ch_B.*")

    assert rows["level"]["command"] == "bench.setpoint.cmd"
    assert rows["level"]["measurement"] == "bench.setpoint"
    assert rows["spectrum"]["command"] is None and rows["spectrum"]["measurement"] == "bench.spectrum"
    assert rows["mode"]["command"] is None and rows["mode"]["measurement"] is None


@pytest.mark.parametrize(
    "channels, message",
    [
        ({"levle": "level"}, "does not have"),
        ({"level": "x", "mode": "x"}, "same channel name"),
        ({"level": "level.cmd"}, ".cmd"),
        ({"spectrum": "bins", "level": "bins.0"}, "numbered channels"),
    ],
)
def test_invalid_channel_map_is_rejected(channels, message):
    with expected_protocol(Bench, []) as bench:
        with pytest.raises(ValueError, match=message):
            attach(bench, "bench", channel_map=ChannelMap(channels=channels))


def test_detach_restores_the_original_classes_and_attach_leaves_device_open():
    publisher = RecordingPublisher()
    with expected_protocol(Bench, [("LEV 3", None)]) as bench:
        recorder = attach(bench, "bench", [publisher])
        assert isinstance(bench, Bench) and type(bench) is not Bench
        recorder.close()
        assert type(bench) is Bench and type(bench.ch_A) is Probe
        assert publisher.closed and bench.adapter.connection is not None
        bench.level = 3

    assert publisher.data == []


def test_device_attr_alias_and_double_attach():
    with expected_protocol(Bench, []) as bench:
        wrapper = PymeasureInstrument("bench", bench, device_attr="bench", close_device=False)
        assert wrapper.bench is wrapper.device is bench
        with pytest.raises(RuntimeError, match="already attached"):
            attach(bench, "again")


class ConstantAdapter(Adapter):
    def _write(self, command, **kwargs):
        pass

    def _read(self, **kwargs):
        return "21.5"


def test_poll_reads_in_the_background_and_feeds_get_channel():
    with PymeasureInstrument(
        "bench",
        Bench(ConstantAdapter()),
        poll=["ch_A.temperature"],
        background_config=BackgroundDaemonConfig(interval=0.01),
    ) as bench:
        bench.start()
        assert bench.get_channel("chA.temperature", wait_for_new_samples=True, timeout=2).latest == 21.5


class FailingPublisher(RecordingPublisher):
    def publish(self, data, **kwargs):
        raise OSError("disk full")


def test_publisher_failure_does_not_fail_the_instrument_access(caplog):
    with expected_protocol(Bench, [("LEV 2", None), ("LEV?", "2")]) as bench:
        attach(instrument=bench, name="bench", publishers=[FailingPublisher()])
        bench.level = 2
        assert bench.level == 2

    assert "Failed to publish write of 'level'" in caplog.text
    assert "Failed to publish read of 'level'" in caplog.text


def test_failed_attach_leaves_the_instrument_reusable(monkeypatch):
    from instro.pymeasure import wrapper

    real = wrapper._published_class

    def refuse_channels(cls):
        if issubclass(cls, Probe):
            raise TypeError("cannot publish this class")
        return real(cls)

    with expected_protocol(Bench, []) as bench:
        monkeypatch.setattr(wrapper, "_published_class", refuse_channels)
        with pytest.raises(TypeError):
            attach(instrument=bench, name="bench")
        assert type(bench) is Bench

        monkeypatch.setattr(wrapper, "_published_class", real)
        attach(instrument=bench, name="bench").detach()


def test_detach_stops_background_polling():
    recorder = PymeasureInstrument(
        name="bench",
        instrument=Bench(ConstantAdapter()),
        poll=["ch_A.temperature"],
        background_config=BackgroundDaemonConfig(interval=0.01),
    )
    recorder.start()
    recorder.get_channel("chA.temperature", wait_for_new_samples=True, timeout=2)
    recorder.detach()

    assert recorder._background_thread is not None and not recorder._background_thread.is_alive()
    recorder.close()
