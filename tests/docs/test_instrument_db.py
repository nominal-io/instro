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


def test_docstring_examples_take_python_blocks_and_skip_other_languages():
    from instrument_db.sources import docstring_examples

    doc = (
        "A thing.\n\n.. code-block:: python\n    :linenos:\n\n    smu = Thing(1)\n    smu.reset()\n\n"
        ".. code-block:: console\n\n    $ not python\n\nUsage::\n\n    thing.read()\n\nAfter."
    )

    assert docstring_examples(doc) == ["smu = Thing(1)\nsmu.reset()", "thing.read()"]


def test_with_instro_attaches_after_construction_and_keeps_the_rest():
    from instrument_db.render import with_instro

    code = "smu = Keithley2400(\n    'GPIB::1',\n)\nsmu.reset()\nprint(smu.voltage)"

    adapted = with_instro(_smu(), code)

    assert adapted.splitlines() == [
        "from pymeasure.instruments.keithley import Keithley2400",
        "from instro.lib import NominalCorePublisher",
        "from instro.pymeasure import attach",
        "",
        "smu = Keithley2400(",
        "    'GPIB::1',",
        ")",
        'recorder = attach(smu, "smu", [NominalCorePublisher(dataset_rid="<dataset rid>")])',
        "smu.reset()",
        "print(smu.voltage)",
        "",
        "recorder.close()  # flush and close the publishers",
    ]


def test_with_wrapper_moves_the_example_inside_pymeasure_instrument():
    from instrument_db.render import with_wrapper

    code = "from pymeasure.instruments.keithley import Keithley2400\n\nsmu = Keithley2400('GPIB::1')\nsmu.reset()\nx = 1\nprint(smu.voltage)"

    adapted = with_wrapper(_smu(), code)

    assert adapted.splitlines() == [
        "from pymeasure.instruments.keithley import Keithley2400",
        "from instro.pymeasure import PymeasureInstrument",
        "",
        "with PymeasureInstrument(",
        '    name="smu",',
        "    instrument=Keithley2400('GPIB::1'),",
        '    dataset_rid="<dataset rid>",',
        ") as smu:",
        "    smu.device.reset()",
        "    x = 1",
        "    print(smu.device.voltage)",
    ]


def test_with_wrapper_flattens_a_multiline_constructor():
    from instrument_db.render import with_wrapper

    adapted = with_wrapper(
        _smu(), 'smu = Keithley2400("GPIB::1",\n                  timeout=5000,\n                  )\nsmu.reset()'
    )

    assert "    instrument=Keithley2400('GPIB::1', timeout=5000)," in adapted


def test_with_instro_leaves_examples_that_never_build_the_driver():
    from instrument_db.render import with_instro, with_wrapper

    assert with_instro(_smu(), "smu.reset()") is None
    assert with_wrapper(_smu(), "smu = Keithley2400(1)") is None  # nothing to run inside the with block
    assert with_instro(_smu(), "this is not python(") is None


def test_stub_shows_the_instro_adapted_docstring_example(tmp_path):
    record = _smu()
    record = InstrumentRecord(
        **{**record.__dict__, "extra": {**record.extra, "examples": ["smu = Keithley2400(1)\nsmu.reset()"]}}
    )

    page = stub_page(record, tmp_path)

    assert "emphasize" not in page
    assert 'recorder = attach(smu, "smu"' in page
    assert "### With a PymeasureInstrument" in page
    assert "### With existing pymeasure code (`attach()`)" in page
    assert ") as smu:" in page
    assert "## Using the pymeasure driver" not in page
