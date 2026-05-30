from importlib.metadata import version

from instro.lib import Instrument
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU


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
