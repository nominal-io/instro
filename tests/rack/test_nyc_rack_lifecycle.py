"""NYC test rack: instro lifecycle and config-driven construction on real transports.

Each check builds its own instruments instead of using the shared ``rack`` fixture, because
it is testing open/close itself. Nothing here enables an output.

Run:
    uv run python tests/rack/test_nyc_rack_lifecycle.py
"""

from __future__ import annotations

import math
import sys

import pytest
from discovery import DiscoveredInstrument
from rack_support import assert_close, latest, logger, run_pytest

from instro.dmm import InstroDMM
from instro.eload import InstroELoad
from instro.psu import InstroPSU

pytestmark = pytest.mark.hardware


def _query_float(instrument: InstroPSU | InstroDMM | InstroELoad, command: str) -> float:
    return float(instrument._driver._visa.query(command))  # type: ignore[union-attr]


def test_close_is_idempotent_and_reopen_works(instruments: dict[str, DiscoveredInstrument]) -> None:
    found = instruments["psu"]
    assert found.num_channels is not None
    psu = InstroPSU(name="lifecycle_psu", driver=found.make_driver(), num_channels=found.num_channels)
    psu.open()
    assert math.isfinite(latest(psu.get_voltage_setpoint(channel=1)))
    psu.close()
    psu.close()
    logger.info("  PASS  second close() is a no-op")

    with pytest.raises(RuntimeError, match="not open") as exc:
        psu.get_voltage_setpoint(channel=1)
    logger.info("  PASS  call on a closed instrument raised: %s", exc.value)

    psu.open()
    try:
        assert math.isfinite(latest(psu.get_voltage_setpoint(channel=1)))
        logger.info("  PASS  reopened and queried")
    finally:
        psu.close()


def test_context_manager_closes_on_exception(instruments: dict[str, DiscoveredInstrument]) -> None:
    dmm = InstroDMM(name="lifecycle_dmm", driver=instruments["dmm"].make_driver())
    with pytest.raises(RuntimeError, match="boom"):
        with dmm:
            assert math.isfinite(latest(dmm.read_dc_voltage()))
            raise RuntimeError("boom")
    assert not dmm._driver._visa.is_open, "transport should be closed after the with-block raised"  # type: ignore[attr-defined]
    logger.info("  PASS  transport closed after exception inside the with-block")


def test_psu_config_construction(instruments: dict[str, DiscoveredInstrument]) -> None:
    found = instruments["psu"]
    config = {
        "version": 1,
        "instrument": "InstroPSU",
        "device": {"name": "cfg_psu", "model": found.model},
        "driver": found.config_driver_block(),
        "timing": {"poll_interval": 0.5},
    }
    psu = InstroPSU(config=config)
    assert psu.name == "cfg_psu" and psu.background_interval == 0.5
    assert found.num_channels is not None
    with psu:
        for ch in range(1, found.num_channels + 1):
            assert math.isfinite(latest(psu.get_voltage_setpoint(channel=ch)))
    logger.info("  PASS  config-built %s opened and queried all %d channels", found.driver_name, found.num_channels)


def test_dmm_config_reapplies_measurement_on_every_open(instruments: dict[str, DiscoveredInstrument]) -> None:
    config = {
        "version": 1,
        "instrument": "InstroDMM",
        "device": {"name": "cfg_dmm"},
        "driver": instruments["dmm"].config_driver_block(),
        "measurement": {"function": "DC_VOLTAGE", "aperture_nplc": 1, "range": 10.0},
    }
    dmm = InstroDMM(config=config)
    with dmm:
        assert math.isfinite(latest(dmm.read())), "config should make read() usable without set_measurement_function"
        assert_close("DCV range from config", _query_float(dmm, "VOLT:DC:RANG?"), 10.0, 0, 0.001)
        assert_close("DCV NPLC from config", _query_float(dmm, "VOLT:DC:NPLC?"), 1.0, 0, 0.001)
        dmm._driver._visa.write("VOLT:DC:RANG:AUTO ON")  # type: ignore[attr-defined]
        dmm._driver._visa.write("VOLT:DC:NPLC 10")  # type: ignore[attr-defined]

    with dmm:
        assert_close("DCV range re-applied", _query_float(dmm, "VOLT:DC:RANG?"), 10.0, 0, 0.001)
        assert_close("DCV NPLC re-applied", _query_float(dmm, "VOLT:DC:NPLC?"), 1.0, 0, 0.001)
        assert math.isfinite(latest(dmm.read()))


def test_eload_config_applies_load_block_without_enabling_input(instruments: dict[str, DiscoveredInstrument]) -> None:
    config = {
        "version": 1,
        "instrument": "InstroELoad",
        "device": {"name": "cfg_eload"},
        "driver": instruments["eload"].config_driver_block(),
        "load": {"mode": "CC", "level": 0.1, "range": 3.0, "slew_rate": {"direction": "BOTH", "rate": 0.1}},
    }
    eload = InstroELoad(config=config)
    with eload:
        function = eload._driver._visa.query("FUNC?").strip().upper()  # type: ignore[attr-defined]
        logger.info("  eload FUNC? -> %s", function)
        assert function.startswith("CURR"), f"config mode CC not applied, device reports {function}"
        assert_close("CC level from config", _query_float(eload, "CURR?"), 0.1, 0, 0.001)
        input_state = eload._driver._visa.query("INP?").strip().upper()  # type: ignore[attr-defined]
        logger.info("  eload INP? -> %s", input_state)
        assert input_state in {"0", "OFF"}, f"config must never enable the input, device reports {input_state}"
        eload.set_level(0.2)
        assert_close("set_level works after config (mode cached)", _query_float(eload, "CURR?"), 0.2, 0, 0.001)


if __name__ == "__main__":
    sys.exit(run_pytest(__file__))
