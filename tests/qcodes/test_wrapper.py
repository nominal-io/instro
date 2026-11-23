import numpy as np
import pytest
from qcodes.instrument import ChannelList, Instrument, InstrumentChannel
from qcodes.instrument_drivers.mock_instruments import DummyChannelInstrument, DummyInstrument
from qcodes.validators import Numbers

from instro.lib.types import BackgroundDaemonConfig, Command, Measurement
from instro.qcodes import ChannelMap, QcodesInstrument, attach, describe


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


class Probe(InstrumentChannel):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        self.add_parameter("temperature", get_cmd=lambda: 21.5, unit="K")


class Bench(Instrument):
    """Small driver covering the parameter shapes the wrapper has to handle."""

    def __init__(self, name):
        super().__init__(name)
        self.state = {"level": 0.0, "mode": "off"}
        self.writes: list[str] = []
        self.add_parameter(
            "level",
            get_cmd=lambda: self.state["level"],
            set_cmd=lambda v: self.state.update(level=v),
            vals=Numbers(0, 10),
            unit="V",
            label="Level",
        )
        self.add_parameter(
            "mode",
            get_cmd=lambda: self.state["mode"],
            set_cmd=lambda v: self.state.update(mode=v),
            val_mapping={"on": 1, "off": 0},
        )
        self.add_parameter("spectrum", get_cmd=lambda: [1.0, 2.0, 3.0])
        self.add_parameter("trace", get_cmd=lambda: np.arange(100.0))
        self.add_parameter("trigger", get_cmd=False, set_cmd=lambda v: None, label="Trigger")
        self.add_submodule("probe", Probe(self, "probe"))
        probes = ChannelList(self, "probes", Probe)
        for label in ("x", "y"):
            probes.append(Probe(self, f"probe_{label}"))
        self.add_submodule("probes", probes)

    def write_raw(self, cmd):
        self.writes.append(cmd)

    def ask_raw(self, cmd):
        return "reply"

    def ramp(self, target: float) -> None:
        self.level(target / 2)
        self.level(target)


@pytest.fixture(autouse=True)
def _close_instruments():
    yield
    Instrument.close_all()


@pytest.fixture
def bench():
    return Bench("bench")


def test_set_publishes_command_and_get_publishes_measurement():
    publisher = RecordingPublisher()
    dac = DummyInstrument("dac", gates=["g1", "g2"])
    recorder = attach(dac, "dac", [publisher])

    dac.g1(1.5)
    assert dac.g1() == 1.5

    assert publisher.channels() == {"dac.g1.cmd": [1.5], "dac.g1": [1.5]}
    command = publisher.data[0]
    assert isinstance(command, Command)
    assert command.tags["qcodes-class"] == "DummyInstrument"
    recorder.close()


def test_every_access_path_publishes(bench):
    publisher = RecordingPublisher()
    attach(bench, "bench", [publisher])

    bench.level.set(1)  # .set()
    bench.level(2)  # call syntax
    bench.ramp(8)  # parameter access inside a driver method

    assert publisher.channels()["bench.level.cmd"] == [1.0, 2.0, 4.0, 8.0]
    bench.level.get()
    bench.level()
    assert publisher.channels()["bench.level"] == [8.0, 8.0]


def test_submodules_and_channel_lists_publish_under_their_path(bench):
    publisher = RecordingPublisher()
    attach(bench, "bench", [publisher])

    bench.probe.temperature()
    bench.probes[0].temperature()

    assert publisher.channels() == {"bench.probe.temperature": [21.5], "bench.probes.probe_x.temperature": [21.5]}


def test_value_shapes(bench):
    publisher = RecordingPublisher()
    attach(bench, "bench", [publisher])

    bench.mode("on")  # the value the user passed, not the instrument's 1
    assert bench.mode() == "on"
    bench.spectrum()
    bench.trace()  # too long to publish

    assert publisher.channels() == {
        "bench.mode.cmd": ["on"],
        "bench.mode": ["on"],
        "bench.spectrum.0": [1.0],
        "bench.spectrum.1": [2.0],
        "bench.spectrum.2": [3.0],
    }


def test_failed_set_publishes_nothing(bench):
    publisher = RecordingPublisher()
    attach(bench, "bench", [publisher])

    with pytest.raises(ValueError):
        bench.level(11)  # fails validation

    assert publisher.channels() == {}


def test_publisher_failure_does_not_break_the_instrument(bench):
    class Broken(RecordingPublisher):
        def publish(self, data, **kwargs):
            raise RuntimeError("boom")

    attach(bench, "bench", [Broken()])

    bench.level(4)

    assert bench.level() == 4


def test_base_parameters_are_not_published(bench):
    assert "IDN" not in {spec.path for spec in describe(bench)}
    assert "timeout" not in {spec.path for spec in describe(bench)}


def test_describe_reports_kind_unit_and_label(bench):
    specs = {spec.path: spec for spec in describe(bench)}

    assert (specs["level"].kind, specs["level"].unit, specs["level"].label) == ("control", "V", "Level")
    assert specs["probe.temperature"].kind == "measurement"
    assert specs["trigger"].kind == "setting"
    assert "probes.probe_x.temperature" in specs


def test_channels_registered_twice_are_listed_once():
    channels = DummyChannelInstrument("chans", channel_names=["A", "B"])

    paths = [spec.path for spec in describe(channels)]

    assert len(paths) == len(set(paths))
    assert "A.temperature" in paths and "B.temperature" in paths


def test_channel_map_renames_and_excludes(bench):
    publisher = RecordingPublisher()
    channel_map = ChannelMap.generate(bench).rename({"level": "out.v"}).exclude("probe*", "mode", "trigger")
    attach(bench, "bench", [publisher], channel_map=channel_map)

    bench.level(1)
    bench.mode("on")
    bench.probe.temperature()

    assert publisher.channels() == {"bench.out.v.cmd": [1.0]}


def test_channel_map_round_trips_through_a_file(bench, tmp_path):
    path = tmp_path / "map.json"
    ChannelMap.generate(bench).rename({"level": "out.v"}).save(path, bench)

    loaded = ChannelMap.load(path)

    assert loaded.channels["level"] == "out.v"
    assert '"unit": "V"' in path.read_text()


def test_channel_map_rejects_unknown_parameters(bench):
    with pytest.raises(ValueError, match="does not have"):
        QcodesInstrument("bench", bench, [], channel_map={"channels": {"nope": "x"}})


def test_channel_map_rejects_duplicate_and_cmd_names(bench):
    with pytest.raises(ValueError, match="same channel name"):
        QcodesInstrument("bench", bench, [], channel_map={"channels": {"level": "a", "mode": "a"}})
    bench2 = Bench("bench2")
    with pytest.raises(ValueError, match=r"\.cmd"):
        QcodesInstrument("bench2", bench2, [], channel_map={"channels": {"level": "a.cmd"}})


def test_preview_lists_command_and_measurement_channels(bench):
    rows = {row["parameter"]: row for row in ChannelMap.defaults().preview(bench, "bench")}

    assert rows["level"]["command"] == "bench.level.cmd"
    assert rows["level"]["measurement"] == "bench.level"
    assert rows["probe.temperature"]["command"] is None
    assert rows["trigger"]["measurement"] is None


def test_poll_reads_parameters_in_the_background(bench):
    publisher = RecordingPublisher()
    with QcodesInstrument(
        "bench",
        bench,
        [publisher],
        poll=["probe.temperature"],
        background_config=BackgroundDaemonConfig(),
        background_interval=0.01,
    ) as wrapper:
        wrapper.start()
        wrapper.get_channel("probe.temperature", wait_for_new_samples=True)

    assert publisher.channels()["bench.probe.temperature"]


def test_poll_rejects_unreadable_and_unknown_paths(bench):
    with pytest.raises(ValueError, match="not a gettable"):
        QcodesInstrument("bench", bench, [], poll=["trigger"])
    with pytest.raises(ValueError, match="not a gettable"):
        QcodesInstrument("bench", bench, [], poll=["nope"])


def test_detach_restores_the_instrument(bench):
    publisher = RecordingPublisher()
    original_get, original_set = bench.level.get, bench.level.set
    recorder = attach(bench, "bench", [publisher])
    assert bench.level.get is not original_get

    recorder.close()

    assert bench.level.get is original_get and bench.level.set is original_set
    assert "write" not in bench.__dict__ and "ask" not in bench.__dict__
    bench.level(5)
    assert publisher.channels() == {}


def test_a_second_attach_is_rejected_until_detached(bench):
    first = attach(bench, "a", [RecordingPublisher()])

    with pytest.raises(RuntimeError, match="already attached"):
        attach(bench, "b", [RecordingPublisher()])

    first.close()
    attach(bench, "b", [RecordingPublisher()]).close()


def test_close_closes_the_device_only_when_asked():
    kept = DummyInstrument("kept", gates=["g1"])
    attach(kept, "kept", []).close()
    assert Instrument.exist("kept")

    owned = DummyInstrument("owned", gates=["g1"])
    QcodesInstrument("owned", owned, []).close()
    assert not Instrument.exist("owned")


def test_direct_io_is_serialized_with_the_lock(bench):
    wrapper = QcodesInstrument("bench", bench, [], close_device=False)

    assert bench.ask("*IDN?") == "reply"
    bench.write("OUT ON")

    assert bench.writes == ["OUT ON"]
    wrapper.close()


def test_device_attr_adds_an_alias(bench):
    wrapper = QcodesInstrument("bench", bench, [], device_attr="smu")

    assert wrapper.smu is bench and wrapper.device is bench
    with pytest.raises(ValueError, match="free attribute"):
        QcodesInstrument("bench", Bench("other"), [], device_attr="name")
