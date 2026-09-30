"""NYC test rack: instrument behavior beyond steady-state agreement.

Protection trips, the PSU's CV->CC crossover, the eload's CV/CP/short modes, and whether DMM
NPLC actually changes the integration.
Every level stays at or below 5 V / 1 A. Trip checks restore the standard protection
levels (rack_support.OVP_V/OCP_A) and clear the trip in ``finally``.

instro has no API to query or clear a protection trip, so those two steps use raw SCPI
(``:OUTP:OVP|OCP:QUES?`` / ``:CLEAR``) and are logged as such. ``RigolDP800.query_status``
can't stand in: its OVP/OCP flags decode the questionable *condition* register, which drops
back to 0 the moment the trip turns the output off, so they read False on a tripped channel.

Run:
    uv run python tests/rack/test_nyc_rack_behavior.py
"""

from __future__ import annotations

import statistics
import sys
import time

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
    PSU_READBACK_I_ABS_A,
    PSU_READBACK_I_REL,
    SETTLE_S,
    Rack,
    arm_protection,
    assert_below,
    assert_close,
    latest,
    logger,
    psu_mode,
    run_pytest,
    wait_for_psu_readback,
)

from instro.dmm.types import MeasurementFunction
from instro.eload.types import LoadMode
from instro.psu import InstroPSU

pytestmark = pytest.mark.hardware

TRIP_TIMEOUT_S = 3.0
CROSSOVER_LIMIT_A = 0.3
CROSSOVER_CR_OHM = 5.0  # wants 1 A at 5 V, so the 0.3 A limit forces the PSU into CC at ~1.5 V
ELOAD_CV_V = 3.0
ELOAD_CP_W = 1.0
SHORT_LIMIT_A = 0.2
POWER_LINE_HZ = 60.0


def _trip_latched(psu: InstroPSU, flag: str, channel: int) -> bool:
    reply = psu._driver._visa.query(f":OUTP:{flag}:QUES? CH{channel}").strip().upper()  # type: ignore[attr-defined]
    return reply == "YES"


def _wait_for_trip(psu: InstroPSU, flag: str, channel: int) -> bool:
    """Poll the latched trip query until ``flag`` (OVP/OCP) trips or the timeout passes."""
    start = time.perf_counter()
    while not (tripped := _trip_latched(psu, flag, channel)) and time.perf_counter() - start < TRIP_TIMEOUT_S:
        time.sleep(0.1)
    logger.info("  raw :OUTP:%s:QUES? CH%d -> %s after %.2f s", flag, channel, tripped, time.perf_counter() - start)
    status = psu._driver.query_status()[f"ch{channel}"]  # type: ignore[attr-defined]
    logger.info("  instro query_status() CH%d: %s (condition register; doesn't latch trips)", channel, status)
    return tripped


def _clear_trip(psu: InstroPSU, flag: str, channel: int) -> None:
    visa = psu._driver._visa  # type: ignore[attr-defined]
    visa.write(f":OUTP:{flag}:CLEAR CH{channel}")
    logger.info("  raw SCPI :OUTP:%s:CLEAR CH%d -> SYST:ERR? %s", flag, channel, visa.query(":SYST:ERR?").strip())


def test_ocp_trips_output_and_clears(rack: Rack) -> None:
    psu, dmm = rack.psu, rack.dmm
    dmm.set_measurement_function(MeasurementFunction.DC_CURRENT)
    dmm.set_range(LOOP_DCI_RANGE_A)
    psu.set_overcurrent_protection_level(0.005, channel=LOOP_CH)
    try:
        logger.info("  CH2 OCP 5 mA, driving ~10 mA into the loop")
        psu.apply(current_limit=LOOP_CURRENT_LIMIT_A, voltage=1.0, enable=True, channel=LOOP_CH)
        assert _wait_for_trip(psu, "OCP", LOOP_CH), "OCP never tripped at ~10 mA with a 5 mA level"
        assert latest(psu.get_output_status(channel=LOOP_CH)) == 0.0, "OCP trip should turn the output off"
        time.sleep(SETTLE_S)  # output capacitance discharges through the loop for ~0.1 s after the trip
        assert_below("DMM loop current after OCP trip", latest(dmm.read_dc_current()), OFF_THRESHOLD_A)
    finally:
        _clear_trip(psu, "OCP", LOOP_CH)
        arm_protection(psu)
    assert not _trip_latched(psu, "OCP", LOOP_CH), "OCP trip still latched after clear"


def test_ovp_trips_output_and_clears(rack: Rack) -> None:
    psu, dmm = rack.psu, rack.dmm
    psu.set_overvoltage_protection_level(3.0, channel=BUS_CH)
    try:
        psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=2.5, enable=True, channel=BUS_CH)
        wait_for_psu_readback(psu, BUS_CH, 2.5, 0.005, 0.02)
        logger.info("  CH1 OVP 3.0 V, raising the setpoint to 3.5 V")
        psu.set_voltage(3.5, channel=BUS_CH)
        assert _wait_for_trip(psu, "OVP", BUS_CH), "OVP never tripped after setting 3.5 V with a 3.0 V level"
        assert latest(psu.get_output_status(channel=BUS_CH)) == 0.0, "OVP trip should turn the output off"
        time.sleep(SETTLE_S)
        assert_below("DMM bus after OVP trip", latest(dmm.read_dc_voltage()), OFF_THRESHOLD_V)
    finally:
        psu.set_voltage(1.0, channel=BUS_CH)
        _clear_trip(psu, "OVP", BUS_CH)
        arm_protection(psu)
    assert not _trip_latched(psu, "OVP", BUS_CH), "OVP trip still latched after clear"


def test_psu_cv_to_cc_crossover(rack: Rack) -> None:
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    eload.set_mode(LoadMode.CR)
    eload.set_level(CROSSOVER_CR_OHM)
    psu.apply(current_limit=CROSSOVER_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    assert psu_mode(psu, BUS_CH) == "CV", "CH1 should be in CV before the load comes on"

    eload.output_enable(True)
    time.sleep(SETTLE_S)
    mode = psu_mode(psu, BUS_CH)
    logger.info("  PSU CH1 mode with CR %.1f Ω on: %s", CROSSOVER_CR_OHM, mode)
    assert mode == "CC", (
        f"CR {CROSSOVER_CR_OHM} Ω at {BUS_VOLTAGE_V} V exceeds {CROSSOVER_LIMIT_A} A; expected CC, got {mode}"
    )

    psu_current = latest(psu.get_current(channel=BUS_CH))
    load_voltage = latest(eload.get_voltage())
    load_current = latest(eload.get_current())
    bus = latest(dmm.read_dc_voltage())
    assert_close("PSU current at its limit", psu_current, CROSSOVER_LIMIT_A, PSU_READBACK_I_REL, PSU_READBACK_I_ABS_A)
    assert_close("eload current", load_current, CROSSOVER_LIMIT_A, 0.02, 0.01)
    assert_close("DMM bus = limit × R", bus, CROSSOVER_LIMIT_A * CROSSOVER_CR_OHM, 0.05, 0.05)
    assert_close("eload voltage vs DMM", load_voltage, bus, 0.01, 0.05)
    assert_close("PSU readback vs DMM", latest(psu.get_voltage(channel=BUS_CH)), bus, 0.005, 0.02)


def test_eload_cv_mode(rack: Rack) -> None:
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    eload.set_mode(LoadMode.CV)
    eload.set_level(ELOAD_CV_V)
    psu.apply(current_limit=CROSSOVER_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    eload.output_enable(True)
    time.sleep(SETTLE_S)

    assert_close("DMM bus held at CV level", latest(dmm.read_dc_voltage()), ELOAD_CV_V, 0.01, 0.05)
    assert_close("eload voltage", latest(eload.get_voltage()), ELOAD_CV_V, 0.02, 0.05)
    assert_close("eload current = PSU limit", latest(eload.get_current()), CROSSOVER_LIMIT_A, 0.02, 0.01)
    mode = psu_mode(psu, BUS_CH)
    assert mode == "CC", f"eload CV below the PSU setpoint should push the PSU into CC, got {mode}"


def test_eload_cp_mode(rack: Rack) -> None:
    psu, eload = rack.psu, rack.eload
    eload.set_mode(LoadMode.CP)
    eload.set_level(ELOAD_CP_W)
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    eload.output_enable(True)
    time.sleep(SETTLE_S)

    voltage = latest(eload.get_voltage())
    current = latest(eload.get_current())
    assert_close("eload power V×I", voltage * current, ELOAD_CP_W, 0.03, 0.02)
    assert_close("PSU current = P/V", latest(psu.get_current(channel=BUS_CH)), ELOAD_CP_W / voltage, 0.02, 0.01)
    mode = psu_mode(psu, BUS_CH)
    assert mode == "CV", f"1 W at 5 V is well under the 1 A limit; expected CV, got {mode}"


def test_eload_short(rack: Rack) -> None:
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    psu.apply(current_limit=SHORT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    try:
        logger.info("  eload short ON with CH1 limited to %.2f A", SHORT_LIMIT_A)
        eload.short_output(True)
        time.sleep(SETTLE_S)
        assert_below("DMM bus while shorted", latest(dmm.read_dc_voltage()), 0.2)
        assert_close("eload current = PSU limit", latest(eload.get_current()), SHORT_LIMIT_A, 0.02, 0.01)
        assert_close(
            "PSU current at its limit",
            latest(psu.get_current(channel=BUS_CH)),
            SHORT_LIMIT_A,
            PSU_READBACK_I_REL,
            PSU_READBACK_I_ABS_A,
        )
        mode = psu_mode(psu, BUS_CH)
        assert mode == "CC", f"a shorted bus should put the PSU in CC, got {mode}"
    finally:
        eload.short_output(False)

    time.sleep(SETTLE_S)
    assert_close("DMM bus recovered after short off", latest(dmm.read_dc_voltage()), BUS_VOLTAGE_V, 0.005, 0.02)
    assert_below("eload current after short off", latest(eload.get_current()), 0.01)


def test_dmm_nplc_changes_integration(rack: Rack) -> None:
    """NPLC 10 must take ~10 line cycles per reading and NPLC 0.02 must not; the noise difference is logged."""
    psu, dmm = rack.psu, rack.dmm
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    dmm.set_range(10.0)

    stats: dict[float, tuple[float, float, float]] = {}
    for nplc, count in ((0.02, 20), (10.0, 6)):
        dmm.set_aperture_nplc(nplc)
        dmm.read()
        start = time.perf_counter()
        readings = [latest(dmm.read()) for _ in range(count)]
        per_read = (time.perf_counter() - start) / count
        stats[nplc] = (statistics.mean(readings), statistics.stdev(readings), per_read)
        logger.info(
            "  NPLC %-5g mean=%.6f V  stdev=%.2e V  %.1f ms/read", nplc, stats[nplc][0], stats[nplc][1], per_read * 1e3
        )
        assert_close(f"mean bus @ NPLC {nplc}", stats[nplc][0], BUS_VOLTAGE_V, 0.005, 0.02)
    dmm.set_aperture_nplc(1)

    integration_s = 10.0 / POWER_LINE_HZ
    assert stats[10.0][2] >= 0.9 * integration_s, (
        f"NPLC 10 read took {stats[10.0][2] * 1e3:.0f} ms; expected >= {0.9 * integration_s * 1e3:.0f} ms"
    )
    assert stats[0.02][2] < stats[10.0][2] / 3, "NPLC 0.02 should read far faster than NPLC 10"
    logger.info("  noise ratio stdev(NPLC 0.02)/stdev(NPLC 10) = %.1f", stats[0.02][1] / max(stats[10.0][1], 1e-12))


if __name__ == "__main__":
    sys.exit(run_pytest(__file__))
