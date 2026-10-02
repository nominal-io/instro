"""NYC test rack: instrument behavior beyond steady-state agreement, for whichever instruments discovery finds.

Protection trips, the PSU's CV->CC crossover, the eload's CV/CP/short modes, and whether DMM
NPLC actually changes the integration. Every level stays at or below 5 V / 1 A. Each check
skips, with the reason, when the discovered instrument or this instro version lacks the
capability it needs:

- Trip checks use ``InstroPSU``'s trip query/clear methods and skip on PSUs whose driver doesn't
  implement them. They restore the rack's protection levels (rack_support.OVP_V/OCP_A) and clear
  the trip in ``finally``.
- Regulation-mode assertions use ``InstroPSU.get_operating_mode`` and are logged as not checked
  when the driver doesn't report a mode.

Run:
    uv run python tests/rack/test_nyc_rack_behavior.py
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from collections.abc import Callable

import pytest
from rack_support import (
    BUS_CH,
    BUS_CURRENT_LIMIT_A,
    BUS_VOLTAGE_V,
    LOOP_CH,
    LOOP_CURRENT_LIMIT_A,
    LOOP_DCI_RANGE_A,
    OFF_THRESHOLD_A,
    OFF_THRESHOLD_V,
    SETTLE_S,
    UNSUPPORTED,
    Rack,
    arm_protection,
    assert_below,
    assert_close,
    assert_mode,
    latest,
    logger,
    optional,
    require,
    require_channel,
    run_pytest,
    wait_for_psu_readback,
)

from instro.dmm.types import MeasurementFunction
from instro.eload.types import LoadMode

pytestmark = pytest.mark.hardware

TRIP_TIMEOUT_S = 3.0
OCP_TRIP_LEVEL_A = 0.005  # the loop draws ~10 mA at 1 V, so this trips
OVP_TRIP_LEVEL_V = 3.0
CROSSOVER_LIMIT_A = 0.3
CROSSOVER_CR_OHM = 5.0  # wants 1 A at 5 V, so the 0.3 A limit forces the PSU into CC at ~1.5 V
ELOAD_CV_V = 3.0
ELOAD_CP_W = 1.0
SHORT_LIMIT_A = 0.2
POWER_LINE_HZ = float(os.environ.get("RACK_LINE_HZ", "60"))


def _wait_for_trip(query: Callable[[], bool], label: str) -> bool:
    """Poll a tripped query until it reports a trip or the timeout passes."""
    start = time.perf_counter()
    while not (tripped := query()) and time.perf_counter() - start < TRIP_TIMEOUT_S:
        time.sleep(0.1)
    logger.info("  %s tripped: %s after %.2f s", label, tripped, time.perf_counter() - start)
    return tripped


def _set_protection_level(call: Callable[[], object], capability: str) -> None:
    """Program a trip level; skip if the PSU can't do it or refuses a level this low."""
    try:
        require(call, capability)
    except (ValueError, RuntimeError) as exc:
        pytest.skip(f"{capability} refused by this PSU: {exc}")


def test_ocp_trips_output_and_clears(rack: Rack) -> None:
    require_channel(rack, LOOP_CH, "the OCP test load (the DC current loop)")
    psu, dmm = rack.psu, rack.dmm
    tripped_query = psu.get_overcurrent_protection_tripped
    clear = psu.clear_overcurrent_protection
    dmm.set_measurement_function(MeasurementFunction.DC_CURRENT)
    optional(lambda: dmm.set_range(LOOP_DCI_RANGE_A), "DMM DCI range")

    require(lambda: latest(tripped_query(channel=LOOP_CH)), "OCP trip query")
    try:
        _set_protection_level(
            lambda: psu.set_overcurrent_protection_level(OCP_TRIP_LEVEL_A, channel=LOOP_CH), "OCP level 5 mA"
        )
        optional(lambda: psu.set_overcurrent_protection_enabled(True, channel=LOOP_CH), "OCP enable")
        logger.info("  CH%d OCP %.0f mA, driving ~10 mA into the loop", LOOP_CH, OCP_TRIP_LEVEL_A * 1e3)
        psu.apply(current_limit=LOOP_CURRENT_LIMIT_A, voltage=1.0, enable=True, channel=LOOP_CH)
        assert _wait_for_trip(lambda: bool(latest(tripped_query(channel=LOOP_CH))), f"CH{LOOP_CH} OCP"), (
            "OCP never tripped at ~10 mA with a 5 mA level"
        )
        assert latest(psu.get_output_status(channel=LOOP_CH)) == 0.0, "OCP trip should turn the output off"
        assert_mode(psu, LOOP_CH, "OFF", "the OCP trip turned the output off")
        time.sleep(SETTLE_S)  # output capacitance discharges through the loop for ~0.1 s after the trip
        assert_below("DMM loop current after OCP trip", latest(dmm.read_dc_current()), OFF_THRESHOLD_A)

        require(lambda: clear(channel=LOOP_CH), "OCP trip clear")
        assert not latest(tripped_query(channel=LOOP_CH)), "OCP still reports tripped after clear"
        logger.info("  output after clear: %s", "ON" if latest(psu.get_output_status(channel=LOOP_CH)) else "OFF")
    finally:
        optional(lambda: clear(channel=LOOP_CH), "OCP trip clear")
        arm_protection(psu, rack.psu_channels)


def test_ovp_trips_output_and_clears(rack: Rack) -> None:
    psu, dmm = rack.psu, rack.dmm
    tripped_query = psu.get_overvoltage_protection_tripped
    clear = psu.clear_overvoltage_protection

    require(lambda: latest(tripped_query(channel=BUS_CH)), "OVP trip query")
    try:
        _set_protection_level(
            lambda: psu.set_overvoltage_protection_level(OVP_TRIP_LEVEL_V, channel=BUS_CH), "OVP level 3 V"
        )
        optional(lambda: psu.set_overvoltage_protection_enabled(True, channel=BUS_CH), "OVP enable")
        psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=2.5, enable=True, channel=BUS_CH)
        wait_for_psu_readback(psu, BUS_CH, 2.5, 0.005, 0.02)

        logger.info("  CH%d OVP %.1f V, raising the setpoint to 3.5 V", BUS_CH, OVP_TRIP_LEVEL_V)
        try:
            psu.set_voltage(3.5, channel=BUS_CH)
        except (ValueError, RuntimeError) as exc:
            # Refusing a setpoint above OVP protects the bus as well as tripping does.
            logger.info("  PSU refused a setpoint above OVP: %s", exc)
            assert_close("DMM bus held at the old setpoint", latest(dmm.read_dc_voltage()), 2.5, 0.005, 0.02)
            return
        assert _wait_for_trip(lambda: bool(latest(tripped_query(channel=BUS_CH))), f"CH{BUS_CH} OVP"), (
            "OVP neither tripped nor refused a 3.5 V setpoint with a 3.0 V level"
        )
        assert latest(psu.get_output_status(channel=BUS_CH)) == 0.0, "OVP trip should turn the output off"
        assert_mode(psu, BUS_CH, "OFF", "the OVP trip turned the output off")
        time.sleep(SETTLE_S)
        assert_below("DMM bus after OVP trip", latest(dmm.read_dc_voltage()), OFF_THRESHOLD_V)

        require(lambda: clear(channel=BUS_CH), "OVP trip clear")
        assert not latest(tripped_query(channel=BUS_CH)), "OVP still reports tripped after clear"
        logger.info("  output after clear: %s", "ON" if latest(psu.get_output_status(channel=BUS_CH)) else "OFF")
    finally:
        optional(lambda: psu.set_voltage(1.0, channel=BUS_CH), "bus setpoint reset")
        optional(lambda: clear(channel=BUS_CH), "OVP trip clear")
        arm_protection(psu, rack.psu_channels)


def test_psu_cv_to_cc_crossover(rack: Rack) -> None:
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    rel, abs_a = rack.psu_current_readback
    require(lambda: eload.set_mode(LoadMode.CR), "eload CR mode")
    eload.set_level(CROSSOVER_CR_OHM)
    psu.apply(current_limit=CROSSOVER_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    assert_mode(psu, BUS_CH, "CV", "no load yet")

    eload.output_enable(True)
    time.sleep(SETTLE_S)
    assert_mode(
        psu, BUS_CH, "CC", f"CR {CROSSOVER_CR_OHM} Ω at {BUS_VOLTAGE_V} V wants more than {CROSSOVER_LIMIT_A} A"
    )

    bus = latest(dmm.read_dc_voltage())
    assert_close("PSU current at its limit", latest(psu.get_current(channel=BUS_CH)), CROSSOVER_LIMIT_A, rel, abs_a)
    assert_close("eload current", latest(eload.get_current()), CROSSOVER_LIMIT_A, 0.02, 0.01)
    assert_close("DMM bus = limit × R", bus, CROSSOVER_LIMIT_A * CROSSOVER_CR_OHM, 0.05, 0.05)
    assert_close("eload voltage vs DMM", latest(eload.get_voltage()), bus, 0.01, 0.05)
    assert_close("PSU readback vs DMM", latest(psu.get_voltage(channel=BUS_CH)), bus, 0.005, 0.02)


def test_eload_cv_mode(rack: Rack) -> None:
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    require(lambda: eload.set_mode(LoadMode.CV), "eload CV mode")
    eload.set_level(ELOAD_CV_V)
    psu.apply(current_limit=CROSSOVER_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    eload.output_enable(True)
    time.sleep(SETTLE_S)

    assert_close("DMM bus held at CV level", latest(dmm.read_dc_voltage()), ELOAD_CV_V, 0.01, 0.05)
    assert_close("eload voltage", latest(eload.get_voltage()), ELOAD_CV_V, 0.02, 0.05)
    assert_close("eload current = PSU limit", latest(eload.get_current()), CROSSOVER_LIMIT_A, 0.02, 0.01)
    assert_mode(psu, BUS_CH, "CC", "an eload holding the bus below the setpoint pulls the PSU to its limit")


def test_eload_cp_mode(rack: Rack) -> None:
    psu, eload = rack.psu, rack.eload
    rel, abs_a = rack.psu_current_readback
    require(lambda: eload.set_mode(LoadMode.CP), "eload CP mode")
    eload.set_level(ELOAD_CP_W)
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    eload.output_enable(True)
    time.sleep(SETTLE_S)

    voltage = latest(eload.get_voltage())
    current = latest(eload.get_current())
    assert_close("eload power V×I", voltage * current, ELOAD_CP_W, 0.03, 0.02)
    assert_close(
        "PSU current = P/V",
        latest(psu.get_current(channel=BUS_CH)),
        ELOAD_CP_W / voltage,
        max(rel, 0.02),
        max(abs_a, 0.01),
    )
    assert_mode(
        psu, BUS_CH, "CV", f"{ELOAD_CP_W} W at {BUS_VOLTAGE_V} V is well under the {BUS_CURRENT_LIMIT_A} A limit"
    )


def test_eload_short(rack: Rack) -> None:
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    rel, abs_a = rack.psu_current_readback
    psu.apply(current_limit=SHORT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    try:
        logger.info("  eload short ON with CH%d limited to %.2f A", BUS_CH, SHORT_LIMIT_A)
        require(lambda: eload.short_output(True), "eload short")
        time.sleep(SETTLE_S)
        assert_below("DMM bus while shorted", latest(dmm.read_dc_voltage()), 0.2)
        assert_close("eload current = PSU limit", latest(eload.get_current()), SHORT_LIMIT_A, 0.02, 0.01)
        assert_close("PSU current at its limit", latest(psu.get_current(channel=BUS_CH)), SHORT_LIMIT_A, rel, abs_a)
        assert_mode(psu, BUS_CH, "CC", "a shorted bus pulls the PSU to its current limit")
    finally:
        try:
            eload.short_output(False)
        except UNSUPPORTED:
            pass

    time.sleep(SETTLE_S)
    assert_close("DMM bus recovered after short off", latest(dmm.read_dc_voltage()), BUS_VOLTAGE_V, 0.005, 0.02)
    assert_below("eload current after short off", latest(eload.get_current()), 0.01)


def test_dmm_nplc_changes_integration(rack: Rack) -> None:
    """NPLC 10 must take ~10 line cycles per reading and NPLC 0.02 must not; the noise difference is logged."""
    psu, dmm = rack.psu, rack.dmm
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    optional(lambda: dmm.set_range(10.0), "DMM DCV range")

    stats: dict[float, tuple[float, float, float]] = {}
    for nplc, count in ((0.02, 20), (10.0, 6)):
        require(lambda: dmm.set_aperture_nplc(nplc), f"DMM DCV NPLC {nplc}")
        dmm.read()
        start = time.perf_counter()
        readings = [latest(dmm.read()) for _ in range(count)]
        per_read = (time.perf_counter() - start) / count
        stats[nplc] = (statistics.mean(readings), statistics.stdev(readings), per_read)
        logger.info(
            "  NPLC %-5g mean=%.6f V  stdev=%.2e V  %.1f ms/read", nplc, stats[nplc][0], stats[nplc][1], per_read * 1e3
        )
        assert_close(f"mean bus @ NPLC {nplc}", stats[nplc][0], BUS_VOLTAGE_V, 0.005, 0.02)
    optional(lambda: dmm.set_aperture_nplc(1), "DMM DCV NPLC 1")

    integration_s = 10.0 / POWER_LINE_HZ
    assert stats[10.0][2] >= 0.9 * integration_s, (
        f"NPLC 10 read took {stats[10.0][2] * 1e3:.0f} ms; expected >= {0.9 * integration_s * 1e3:.0f} ms "
        f"at {POWER_LINE_HZ:g} Hz (set RACK_LINE_HZ if the rack isn't on 60 Hz)"
    )
    assert stats[0.02][2] < stats[10.0][2] / 3, "NPLC 0.02 should read far faster than NPLC 10"
    logger.info("  noise ratio stdev(NPLC 0.02)/stdev(NPLC 10) = %.1f", stats[0.02][1] / max(stats[10.0][1], 1e-12))


if __name__ == "__main__":
    sys.exit(run_pytest(__file__))
