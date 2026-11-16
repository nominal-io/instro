import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "docs" / "_ext"))

import instrument_db  # noqa: E402
from instrument_db.records import InstrumentRecord  # noqa: E402
from instrument_db.render import home_page, rel_url, stub_page  # noqa: E402
from instrument_db.sources import _first_line, pymeasure_category  # noqa: E402


def _scope() -> InstrumentRecord:
    return InstrumentRecord(
        driver_type="instro",
        category="scope",
        manufacturer="Keysight",
        model="1200X",
        name="Keysight1200X",
        import_path="instro.scope.drivers.keysight_1200x.Keysight1200X",
        extra={
            "required_params": ["visa_resource"],
            "param_names": ["visa_resource"],
            "num_channels": 2,
            "import_from": "instro.drivers",
            "instrument_module": "instro",
            "instrument_class": "InstroScope",
        },
    )


def _smu() -> InstrumentRecord:
    return InstrumentRecord(
        driver_type="pymeasure",
        category="dmm",
        manufacturer="Keithley",
        model="2400",
        name="Keithley2400",
        import_path="pymeasure.instruments.keithley.keithley2400.Keithley2400",
        extra={
            "id": "keithley-Keithley2400",
            "vendor_package": "keithley",
            "package_import": "pymeasure.instruments.keithley",
            "required_params": ["adapter"],
            "read_example": "current",
        },
    )


def test_stub_code_follows_the_driver_type(tmp_path):
    instro_page = stub_page(_scope(), tmp_path)
    assert 'driver=Keysight1200X("<visa_resource>")' in instro_page
    assert "num_channels=2" in instro_page

    pymeasure_page = stub_page(_smu(), tmp_path)
    assert "from pymeasure.instruments.keithley import Keithley2400" in pymeasure_page
    assert "print(keithley2400.current)" in pymeasure_page
    assert "PymeasureInstrument(" in pymeasure_page
    assert "InstroScope" not in pymeasure_page


def test_stub_shows_the_picture_found_by_convention(tmp_path):
    (tmp_path / "instruments/images/pymeasure").mkdir(parents=True)
    (tmp_path / "instruments/images/pymeasure/keithley-keithley2400.png").write_bytes(b"")

    assert "![Keithley 2400](/instruments/images/pymeasure/keithley-keithley2400.png)" in stub_page(_smu(), tmp_path)
    assert "![" not in stub_page(_scope(), tmp_path)


def test_home_lists_every_instrument_with_relative_links():
    page = home_page([_scope(), _smu()])

    assert 'href="instro/keysight1200x/"' in page
    assert 'href="pymeasure/keithley-keithley2400/"' in page
    assert rel_url("instruments/type/scope", "instruments/instro/keysight1200x") == "../../instro/keysight1200x/"


@pytest.mark.parametrize(
    ("setting", "env", "expected"),
    [
        ("build", None, "build"),
        ({"pymeasure": "release", "default": "build"}, None, "release"),
        ({"instro": "release"}, None, "build"),
        ("build", "release", "release"),
    ],
)
def test_refresh_mode(monkeypatch, setting, env, expected):
    monkeypatch.delenv(instrument_db.ENV_VAR, raising=False)
    if env:
        monkeypatch.setenv(instrument_db.ENV_VAR, env)

    assert instrument_db.refresh_mode(setting, "pymeasure") == expected


def test_release_mode_reads_the_snapshot_and_refresh_forces_live(tmp_path, monkeypatch):
    monkeypatch.delenv(instrument_db.ENV_VAR, raising=False)
    path = tmp_path / "snapshot.json"
    instrument_db.write_snapshot(path, {"pymeasure": [_smu()]})

    records, live = instrument_db.collect("release", {"pymeasure": {}}, path)
    assert records["pymeasure"] == [_smu()]
    assert live == set()

    monkeypatch.setattr(
        instrument_db, "make_source", lambda name, **_: type("S", (), {"discover": lambda s: [_scope()]})()
    )
    records, live = instrument_db.collect("release", {"pymeasure": {}}, path, refresh=["pymeasure"])
    assert records["pymeasure"] == [_scope()]
    assert live == {"pymeasure"}


def test_snapshot_is_stable_so_refreshes_diff_cleanly(tmp_path):
    path = tmp_path / "snapshot.json"
    assert instrument_db.write_snapshot(path, {"instro": [_scope()]})
    assert not instrument_db.write_snapshot(path, {"instro": [_scope()]})
    assert json.loads(path.read_text())["sources"]["instro"][0]["name"] == "Keysight1200X"


def test_pymeasure_category_uses_overrides_before_keywords():
    assert pymeasure_category("Foo", "A digital oscilloscope") == "scope"
    assert pymeasure_category("Foo", "A digital oscilloscope", {"Foo": "rf"}) == "rf"
    assert pymeasure_category("Foo", "Something unusual") == "other"


def test_first_line_joins_a_wrapped_first_sentence():
    doc = "Represent the Keithley 2400 SourceMeter and provide a\nhigh-level interface. More detail.\n\nLater."

    assert _first_line(doc) == "Represent the Keithley 2400 SourceMeter and provide a high-level interface"
