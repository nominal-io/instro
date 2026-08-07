"""Runs examples/psu/psu_config_simulated.json for real against the local SCPI simulator.

No mocks: a real SimulatedPSUServer listens on the port examples/psu/psu_config_simulated.json
declares, and InstroPSU(config=...) connects to it over a real socket. Proves the one
example config that's supposed to work with no external hardware actually does.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from instro.psu import InstroPSU
from instro.psu.scpi_sim_server import SimulatedPSU as SimulatedPSUSimulator
from instro.psu.scpi_sim_server import SimulatedPSUServer

EXAMPLE_CONFIG = Path(__file__).resolve().parents[3] / "examples" / "psu" / "psu_config_simulated.json"


@pytest.fixture
def simulated_server():
    simulator = SimulatedPSUSimulator(num_channels=1)
    server = SimulatedPSUServer(simulator, host="127.0.0.1", port=5025)
    server.start()
    try:
        yield server
    finally:
        server.shutdown()


def test_psu_config_simulated_example_builds_and_reads(simulated_server, tmp_path, monkeypatch):
    assert EXAMPLE_CONFIG.exists(), f"{EXAMPLE_CONFIG} is missing"

    # The example's FilePublisher uses a relative directory; run from tmp_path so it
    # doesn't leave a psu_data/ directory sitting in the repo root.
    monkeypatch.chdir(tmp_path)

    psu = InstroPSU(config=EXAMPLE_CONFIG)
    with psu:
        voltage = psu.get_voltage(channel=1)
        current = psu.get_current(channel=1)

    assert math.isfinite(voltage.latest)
    assert math.isfinite(current.latest)
    assert psu._config is not None
    assert psu._config.driver.name == "SimulatedPSU"

    published_files = list((tmp_path / "psu_data").glob("*.jsonl"))
    assert published_files, "FilePublisher declared in the example config did not write anything"
