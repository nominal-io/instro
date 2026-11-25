import json

import pytest
from qcodes.instrument import Instrument
from typer.testing import CliRunner

from instro.cli.main import app

runner = CliRunner()
DUMMY = "qcodes.instrument_drivers.mock_instruments.DummyInstrument"


@pytest.fixture(autouse=True)
def _close_instruments():
    yield
    Instrument.close_all()


def test_map_writes_an_editable_map(tmp_path):
    output = tmp_path / "map.json"

    result = runner.invoke(app, ["qcodes", "map", DUMMY, "--arg", "gates=['g1','g2']", "-o", str(output)])

    assert result.exit_code == 0, result.output
    data = json.loads(output.read_text())
    assert data["format"] == "instro-qcodes-channel-map/1"
    assert data["channels"]["g1"]["channel"] == "g1"
    assert data["channels"]["g1"]["unit"] == "V"
    assert not Instrument.exist("instro_inspect")


def test_describe_prints_a_table():
    result = runner.invoke(app, ["qcodes", "describe", DUMMY, "--arg", "gates=['g1']"])

    assert result.exit_code == 0, result.output
    assert "g1" in result.output and "control" in result.output


def test_unknown_class_is_reported():
    result = runner.invoke(app, ["qcodes", "map", "nope.Nothing"])

    assert result.exit_code != 0
    assert "Could not import" in result.output
