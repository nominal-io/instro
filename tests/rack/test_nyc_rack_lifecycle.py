"""NYC test rack: instro lifecycle and config-driven construction, for whichever instruments discovery finds.

Each check builds its own instruments instead of using the shared ``rack`` fixture, because it
is testing open/close and config loading itself. Config effects are checked by behavior through
the HAL (reading speed, current drawn), not by vendor SCPI queries, so any supported model works.
Only the eload config check energizes the bus (5 V, 1 A limit, 0.1-0.2 A drawn); it always ends
with the eload input and PSU output off.

Run:
    uv run python tests/rack/test_nyc_rack_lifecycle.py
"""

from __future__ import annotations

import math
import os
import sys
import time

import pytest
from discovery import DiscoveredInstrument
from rack_support import (
    BUS_CH,
    BUS_CURRENT_LIMIT_A,
    BUS_VOLTAGE_V,
    SETTLE_S,
    UNSUPPORTED,
    assert_below,
    assert_close,
    latest,
    logger,
    run_pytest,
    safe_state,
    wait_for_psu_readback,
)

from instro.dmm import InstroDMM
from instro.eload import InstroELoad
from instro.psu import InstroPSU

pytestmark = pytest.mark.hardware

POWER_LINE_HZ = float(os.environ.get("RACK_LINE_HZ", "60"))
CONFIG_NPLC = 10
CONFIG_CC_A = 0.1


def _psu(found: DiscoveredInstrument, name: str) -> InstroPSU:
    assert found.num_channels is not None
    return InstroPSU(name=name, driver=found.make_driver(), num_channels=found.num_channels)


def test_close_is_idempotent_and_reopen_works(instruments: dict[str, DiscoveredInstrument]) -> None:
    psu = _psu(instruments["psu"], "lifecycle_psu")
    psu.open()
    assert math.isfinite(latest(psu.get_voltage(channel=BUS_CH)))
    psu.close()
    psu.close()
    logger.info("  PASS  second close() is a no-op")

    with pytest.raises(RuntimeError, match="not open") as exc:
        psu.get_voltage(channel=BUS_CH)
    logger.info("  PASS  call on a closed instrument raised: %s", exc.value)

    psu.open()
    try:
        assert math.isfinite(latest(psu.get_voltage(channel=BUS_CH)))
        logger.info("  PASS  reopened and queried")
    finally:
        psu.close()


def test_context_manager_closes_on_exception(instruments: dict[str, DiscoveredInstrument]) -> None:
    dmm = InstroDMM(name="lifecycle_dmm", driver=instruments["dmm"].make_driver())
    with pytest.raises(RuntimeError, match="boom"):
        with dmm:
            assert math.isfinite(latest(dmm.read_dc_voltage()))
            raise RuntimeError("boom")
    with pytest.raises(RuntimeError, match="not open"):
        dmm.read_dc_voltage()
    logger.info("  PASS  instrument closed after an exception inside the with-block")


def test_psu_config_construction(instruments: dict[str, DiscoveredInstrument]) -> None:
    found = instruments["psu"]
    assert found.num_channels is not None
    config = {
        "version": 1,
        "instrument": "InstroPSU",
        "device": {"name": "cfg_psu", "model": found.model},
        "driver": found.config_driver_block(),
        "timing": {"poll_interval": 0.5},
    }
    psu = InstroPSU(config=config)
    assert psu.name == "cfg_psu" and psu.background_interval == 0.5
    with psu:
        for ch in range(1, found.num_channels + 1):
            assert math.isfinite(latest(psu.get_voltage(channel=ch)))
            assert latest(psu.get_output_status(channel=ch)) in (0.0, 1.0)
    logger.info("  PASS  config-built %s opened and queried all %d channel(s)", found.driver_name, found.num_channels)


def _per_read_s(dmm: InstroDMM, count: int = 4) -> float:
    dmm.read()
    start = time.perf_counter()
    for _ in range(count):
        dmm.read()
    return (time.perf_counter() - start) / count


def test_dmm_config_applies_measurement_on_every_open(instruments: dict[str, DiscoveredInstrument]) -> None:
    """A config's measurement block makes read() usable at open, and is re-applied on every reopen."""
    found = instruments["dmm"]
    base = {
        "version": 1,
        "instrument": "InstroDMM",
        "device": {"name": "cfg_dmm"},
        "driver": found.config_driver_block(),
    }
    dmm = InstroDMM(config={**base, "measurement": {"function": "DC_VOLTAGE"}})
    with dmm:
        reading = dmm.read()
        assert "cfg_dmm.dc_voltage" in reading.channel_data, f"config function not applied: {reading.channel_data}"
        assert math.isfinite(latest(reading))
    logger.info("  PASS  read() works at open without set_measurement_function")

    dmm = InstroDMM(config={**base, "measurement": {"function": "DC_VOLTAGE", "aperture_nplc": CONFIG_NPLC}})
    try:
        dmm.open()
    except UNSUPPORTED as exc:
        pytest.skip(f"{found.driver_name} can't take an NPLC from config: {exc}")
    try:
        slow = _per_read_s(dmm)
        dmm.set_aperture_nplc(0.02)
        fast = _per_read_s(dmm)
    finally:
        dmm.close()
    with dmm:
        reapplied = _per_read_s(dmm)
    minimum = 0.9 * CONFIG_NPLC / POWER_LINE_HZ
    logger.info(
        "  per read: config NPLC %d %.0f ms, after NPLC 0.02 %.0f ms, after reopen %.0f ms",
        CONFIG_NPLC,
        slow * 1e3,
        fast * 1e3,
        reapplied * 1e3,
    )
    assert slow >= minimum, f"config NPLC {CONFIG_NPLC} not applied at open ({slow * 1e3:.0f} ms/read)"
    assert fast < slow / 3, "set_aperture_nplc(0.02) should speed reads up"
    assert reapplied >= minimum, f"config NPLC not re-applied on reopen ({reapplied * 1e3:.0f} ms/read)"


def test_eload_config_applies_load_block_without_enabling_input(instruments: dict[str, DiscoveredInstrument]) -> None:
    """A config's load block sets mode and level (proven by the current drawn), but never enables the input."""
    eload = InstroELoad(
        config={
            "version": 1,
            "instrument": "InstroELoad",
            "device": {"name": "cfg_eload"},
            "driver": instruments["eload"].config_driver_block(),
            "load": {"mode": "CC", "level": CONFIG_CC_A},
        }
    )
    found_psu = instruments["psu"]
    psu = _psu(found_psu, "cfg_eload_source")
    assert found_psu.num_channels is not None
    with psu, eload:
        try:
            psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
            wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
            time.sleep(SETTLE_S)
            assert_below("eload current before enabling input", latest(eload.get_current()), 0.01)

            eload.output_enable(True)
            time.sleep(SETTLE_S)
            assert_close("eload current = config CC level", latest(eload.get_current()), CONFIG_CC_A, 0.02, 0.01)

            eload.set_level(2 * CONFIG_CC_A)
            time.sleep(SETTLE_S)
            assert_close(
                "set_level works after config (mode cached)", latest(eload.get_current()), 2 * CONFIG_CC_A, 0.02, 0.01
            )
        finally:
            safe_state(psu, eload, range(1, found_psu.num_channels + 1))


if __name__ == "__main__":
    sys.exit(run_pytest(__file__))
