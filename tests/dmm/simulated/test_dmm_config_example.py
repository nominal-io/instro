"""Runs examples/dmm/dmm_config_simulated.json for real against the local SCPI simulator.

No mocks: a real SimulatedDMMServer listens on the port examples/dmm/dmm_config_simulated.json
declares, and InstroDMM(config=...) connects to it over a real socket. Proves the one
example config that's supposed to work with no external hardware actually does.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from instro.dmm import InstroDMM
from instro.dmm.scpi_sim_server import SimulatedDMM as SimulatedDMMSimulator
from instro.dmm.scpi_sim_server import SimulatedDMMServer

EXAMPLE_CONFIG = Path(__file__).resolve().parents[3] / "examples" / "dmm" / "dmm_config_simulated.json"


@pytest.fixture
def simulated_server():
    simulator = SimulatedDMMSimulator()
    server = SimulatedDMMServer(simulator, host="127.0.0.1", port=5026)
    server.start()
    try:
        yield server
    finally:
        server.shutdown()


def test_dmm_config_simulated_example_builds_and_reads(simulated_server, tmp_path, monkeypatch):
    assert EXAMPLE_CONFIG.exists(), f"{EXAMPLE_CONFIG} is missing"

    # The example's FilePublisher uses a relative directory; run from tmp_path so it
    # doesn't leave a dmm_data/ directory sitting in the repo root.
    monkeypatch.chdir(tmp_path)

    dmm = InstroDMM(config=EXAMPLE_CONFIG)
    with dmm:
        measurement = dmm.read()

    assert math.isfinite(measurement.latest)
    assert dmm._config is not None
    assert dmm._config.driver.name == "SimulatedDMM"

    published_files = list((tmp_path / "dmm_data").glob("*.jsonl"))
    assert published_files, "FilePublisher declared in the example config did not write anything"
