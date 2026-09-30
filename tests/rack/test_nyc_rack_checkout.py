"""NYC test rack checkout: instrument discovery, instro bring-up, and cross-instrument wiring checks.

The fast smoke suite: run it first after any wiring change. Wiring, safety, and capture
layout are described in rack_support.py.

Run:
    uv run python tests/rack/test_nyc_rack_checkout.py
    uv run python tests/rack/test_nyc_rack_checkout.py -k eload   # extra args go to pytest
"""

from __future__ import annotations

import collections
import sys
import time

import pytest
from discovery import CATEGORIES, DiscoveredInstrument, DiscoveryReport
from rack_support import (
    BUS_CH,
    BUS_CURRENT_LIMIT_A,
    BUS_DIVIDER_OHM,
    BUS_SWEEP_V,
    BUS_VOLTAGE_V,
    ELOAD_CC_RANGE_A,
    ELOAD_CC_STEPS_A,
    ELOAD_CR_OHM,
    LOOP_CH,
    LOOP_CURRENT_LIMIT_A,
    LOOP_DCI_RANGE_A,
    LOOP_R_NOMINAL_OHM,
    LOOP_R_REL_TOL,
    LOOP_SWEEP_V,
    OCP_A,
    OFF_THRESHOLD_A,
    OFF_THRESHOLD_V,
    OVP_V,
    POLL_DURATION_S,
    POLL_INTERVAL_S,
    PSU_CHANNELS,
    PSU_READBACK_I_ABS_A,
    PSU_READBACK_I_REL,
    SETTLE_S,
    SPARE_CH,
    Rack,
    assert_below,
    assert_close,
    latest,
    logger,
    psu_mode,
    read_capture,
    run_pytest,
    wait_for_psu_readback,
    wait_for_stable_psu_current,
)

from instro.dmm.types import MeasurementFunction
from instro.eload.types import LoadMode

pytestmark = pytest.mark.hardware


def test_discovery(discovery: DiscoveryReport) -> None:
    logger.info("  supported:")
    for found in discovery.instruments:
        logger.info("    %-5s %-16s %-45s %s", found.category, found.driver_name, found.resource, found.idn)
    logger.info("  unsupported (no in-tree driver): %s", "none" if not discovery.unsupported else "")
    for other in discovery.unsupported:
        logger.info("    %-45s %s", other.resource, other.idn)
    logger.info("  unreachable: %s", "none" if not discovery.unreachable else "")
    for dead in discovery.unreachable:
        logger.info("    %-45s %s", dead.resource, dead.reason)
    missing = [c for c in CATEGORIES if not any(i.category == c for i in discovery.instruments)]
    assert not missing, f"no supported {', '.join(missing)} discovered"


def test_identities(rack: Rack, instruments: dict[str, DiscoveredInstrument]) -> None:
    """Each opened driver answers with the same identity discovery matched, so the rack drives what it found."""
    for category, instrument in zip(CATEGORIES, rack.instruments):
        driver = instrument._driver  # type: ignore[attr-defined]
        idn = driver._visa.query("*IDN?").strip()
        found = instruments[category]
        logger.info("  %-5s %-16s %s", category, found.driver_name, idn)
        assert idn == found.idn, f"{category}: driver answers {idn!r}, discovery saw {found.idn!r}"
        assert type(driver).__name__ == found.driver_name
    for ch, limits in rack.psu._driver._limits.items():  # type: ignore[attr-defined]
        logger.info(
            "  PSU CH%d limits: V %.3f-%.3f, I %.3f-%.3f",
            ch,
            limits.voltage.minimum,
            limits.voltage.maximum,
            limits.current.minimum,
            limits.current.maximum,
        )


def test_psu_setpoints_and_protection(rack: Rack) -> None:
    psu = rack.psu
    for ch in PSU_CHANNELS:
        voltage, current = 1.0 + ch, 0.1 * ch
        psu.apply(current_limit=current, voltage=voltage, channel=ch)
        assert_close(f"CH{ch} voltage setpoint", latest(psu.get_voltage_setpoint(channel=ch)), voltage, 0, 0.001)
        assert_close(f"CH{ch} current setpoint", latest(psu.get_current_setpoint(channel=ch)), current, 0, 0.001)
        assert latest(psu.get_output_status(channel=ch)) == 0.0, f"CH{ch} output should be off after apply()"

    for ch, level in OVP_V.items():
        assert_close(f"CH{ch} OVP level", latest(psu.get_overvoltage_protection_level(channel=ch)), level, 0, 0.01)
        assert latest(psu.get_overvoltage_protection_enabled(channel=ch)) == 1.0, f"CH{ch} OVP not enabled"
    for ch, level in OCP_A.items():
        assert_close(f"CH{ch} OCP level", latest(psu.get_overcurrent_protection_level(channel=ch)), level, 0, 0.001)
        assert latest(psu.get_overcurrent_protection_enabled(channel=ch)) == 1.0, f"CH{ch} OCP not enabled"

    logger.info("  toggling unconnected CH%d output", SPARE_CH)
    psu.apply(current_limit=0.1, voltage=1.0, enable=True, channel=SPARE_CH)
    assert latest(psu.get_output_status(channel=SPARE_CH)) == 1.0, "CH3 output did not turn on"
    wait_for_psu_readback(psu, SPARE_CH, 1.0, 0.01, 0.02)
    assert_below("CH3 open-circuit current", latest(psu.get_current(channel=SPARE_CH)), 0.005)
    psu.output_enable(False, channel=SPARE_CH)
    assert latest(psu.get_output_status(channel=SPARE_CH)) == 0.0, "CH3 output did not turn off"


def test_dmm_reads_zero_with_outputs_off(rack: Rack) -> None:
    dmm = rack.dmm
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    dmm.set_range(None)
    dmm.set_aperture_nplc(1)
    assert_below("DMM DCV (bus), all outputs off", latest(dmm.read()), OFF_THRESHOLD_V)
    dmm.set_measurement_function(MeasurementFunction.DC_CURRENT)
    dmm.set_range(LOOP_DCI_RANGE_A)
    dmm.set_aperture_nplc(1)
    assert_below("DMM DCI (loop), all outputs off", latest(dmm.read()), OFF_THRESHOLD_A)


def test_bus_voltage_sweep(rack: Rack) -> None:
    """CH1 drives the bus with the eload input off; DMM, eload, and PSU readback must agree."""
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_SWEEP_V[0], enable=True, channel=BUS_CH)
    for voltage in BUS_SWEEP_V:
        logger.info("  CH%d (bus) -> %.2f V, divider draws ~%.1f µA", BUS_CH, voltage, voltage / BUS_DIVIDER_OHM * 1e6)
        psu.set_voltage(voltage, channel=BUS_CH)
        wait_for_psu_readback(psu, BUS_CH, voltage, 0.005, 0.02)
        assert_close(f"DMM DCV (bus) @ {voltage} V", latest(dmm.read_dc_voltage()), voltage, 0.005, 0.02)
        assert_close(f"eload voltage @ {voltage} V", latest(eload.get_voltage()), voltage, 0.02, 0.05)
        assert_below(f"PSU CH{BUS_CH} current (divider only)", latest(psu.get_current(channel=BUS_CH)), 0.005)

    psu.output_enable(False, channel=BUS_CH)
    time.sleep(SETTLE_S)
    assert_below("DMM DCV after CH1 off", latest(dmm.read_dc_voltage()), OFF_THRESHOLD_V)


def test_dc_current_loop(rack: Rack) -> None:
    """CH2 drives the 100 Ω loop into the DMM 3A input; DMM DCI must match the PSU's current readback."""
    psu, dmm = rack.psu, rack.dmm
    dmm.set_measurement_function(MeasurementFunction.DC_CURRENT)
    dmm.set_range(LOOP_DCI_RANGE_A)
    psu.apply(current_limit=LOOP_CURRENT_LIMIT_A, voltage=LOOP_SWEEP_V[0], enable=True, channel=LOOP_CH)
    for voltage in LOOP_SWEEP_V:
        logger.info("  CH%d (loop) -> %.2f V, expect ~%.1f mA", LOOP_CH, voltage, voltage / LOOP_R_NOMINAL_OHM * 1e3)
        psu.set_voltage(voltage, channel=LOOP_CH)
        psu_voltage = wait_for_psu_readback(psu, LOOP_CH, voltage, 0.005, 0.02)
        psu_current = wait_for_stable_psu_current(psu, LOOP_CH)
        dmm_current = latest(dmm.read_dc_current())
        assert_close(
            f"DMM DCI vs PSU CH{LOOP_CH} @ {voltage} V",
            dmm_current,
            psu_current,
            PSU_READBACK_I_REL,
            PSU_READBACK_I_ABS_A,
        )
        assert dmm_current > OFF_THRESHOLD_A, f"no loop current at {voltage} V; is the 100 Ω resistor in circuit?"
        loop_ohm = psu_voltage / dmm_current
        assert_close(f"loop resistance @ {voltage} V", loop_ohm, LOOP_R_NOMINAL_OHM, LOOP_R_REL_TOL, 0)
        mode = psu_mode(psu, LOOP_CH)
        assert mode == "CV", f"PSU CH{LOOP_CH} should regulate voltage below its current limit, got {mode}"

    psu.output_enable(False, channel=LOOP_CH)
    time.sleep(SETTLE_S)
    assert_below("DMM DCI after CH2 off", latest(dmm.read_dc_current()), OFF_THRESHOLD_A)


def test_bus_and_loop_isolated(rack: Rack) -> None:
    """Each channel energized alone must not show up on the other net."""
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    logger.info("  bus on, loop off")
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    assert_below("DMM DCI (loop) with only CH1 on", latest(dmm.read_dc_current()), OFF_THRESHOLD_A)
    assert_below(f"PSU CH{LOOP_CH} readback with only CH1 on", latest(psu.get_voltage(channel=LOOP_CH)), 0.1)
    psu.output_enable(False, channel=BUS_CH)

    logger.info("  loop on, bus off")
    psu.apply(current_limit=LOOP_CURRENT_LIMIT_A, voltage=LOOP_SWEEP_V[-1], enable=True, channel=LOOP_CH)
    wait_for_psu_readback(psu, LOOP_CH, LOOP_SWEEP_V[-1], 0.005, 0.02)
    assert_below("DMM DCV (bus) with only CH2 on", latest(dmm.read_dc_voltage()), OFF_THRESHOLD_V)
    assert_below("eload voltage with only CH2 on", latest(eload.get_voltage()), OFF_THRESHOLD_V * 2)


def test_eload_cc_draw_from_bus(rack: Rack) -> None:
    psu, eload = rack.psu, rack.eload
    eload.set_mode(LoadMode.CC)
    eload.set_range(ELOAD_CC_RANGE_A)
    eload.set_level(ELOAD_CC_STEPS_A[0])
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    eload.output_enable(True)

    for current in ELOAD_CC_STEPS_A:
        logger.info("  eload CC -> %.3f A", current)
        eload.set_level(current)
        time.sleep(SETTLE_S)
        assert_close(f"eload current @ {current} A", latest(eload.get_current()), current, 0.02, 0.01)
        assert_close(
            f"PSU CH{BUS_CH} current @ {current} A", latest(psu.get_current(channel=BUS_CH)), current, 0.02, 0.01
        )
        assert_close(
            f"PSU CH{BUS_CH} voltage @ {current} A",
            latest(psu.get_voltage(channel=BUS_CH)),
            BUS_VOLTAGE_V,
            0.005,
            0.02,
        )
        assert_close(f"eload voltage @ {current} A", latest(eload.get_voltage()), BUS_VOLTAGE_V, 0.02, 0.05)
        assert_close(f"DMM DCV (bus) @ {current} A", latest(rack.dmm.read_dc_voltage()), BUS_VOLTAGE_V, 0.01, 0.05)
        mode = psu_mode(psu, BUS_CH)
        logger.info("  PSU CH%d regulation mode: %s", BUS_CH, mode)
        assert mode == "CV", f"PSU CH{BUS_CH} should stay in CV below its current limit, got {mode}"


def test_eload_cr_draw_from_bus(rack: Rack) -> None:
    psu, eload = rack.psu, rack.eload
    eload.set_mode(LoadMode.CR)
    eload.set_level(ELOAD_CR_OHM)
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    eload.output_enable(True)
    time.sleep(SETTLE_S)

    load_voltage = latest(eload.get_voltage())
    expected_current = load_voltage / ELOAD_CR_OHM
    logger.info("  CR %.1f Ω at %.3f V -> expect %.4f A", ELOAD_CR_OHM, load_voltage, expected_current)
    assert_close("eload current (V/R)", latest(eload.get_current()), expected_current, 0.03, 0.01)
    assert_close(f"PSU CH{BUS_CH} current (V/R)", latest(psu.get_current(channel=BUS_CH)), expected_current, 0.03, 0.01)


def test_background_polling_publishes_to_capture(rack: Rack, request: pytest.FixtureRequest) -> None:
    psu, dmm = rack.psu, rack.dmm
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=3.0, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, 3.0, 0.005, 0.02)

    start_ns = time.time_ns()
    for instrument in rack.instruments:
        instrument.background_interval = POLL_INTERVAL_S
    logger.info("  polling all instruments every %.2f s for %.1f s", POLL_INTERVAL_S, POLL_DURATION_S)
    try:
        for instrument in rack.instruments:
            instrument.start()
        time.sleep(POLL_DURATION_S)
    finally:
        for instrument in rack.instruments:
            instrument.stop()

    counts: collections.Counter[str] = collections.Counter()
    bus_samples: list[float] = []
    for record in read_capture(rack.capture_path, request.node.name):
        stamps = record.get("timestamps") or [record.get("timestamp", 0)]
        if min(stamps) >= start_ns:
            counts.update(record["channel_data"].keys())
            bus_samples.extend(record["channel_data"].get("dmm.dc_voltage", []))
    for channel, count in sorted(counts.items()):
        logger.info("  %-32s %3d record(s)", channel, count)

    logger.info("  DMM bus samples during polling: %s", ", ".join(f"{v:.3f}" for v in bus_samples))
    assert bus_samples and all(abs(v - 3.0) < 0.05 for v in bus_samples), "polled DMM bus voltage should sit at 3 V"

    min_polls = int(POLL_DURATION_S / POLL_INTERVAL_S) // 2
    for channel in ("psu.ch1.voltage", "psu.ch2.voltage", "dmm.dc_voltage", "eload.ch1.voltage", "eload.ch1.current"):
        assert counts[channel] >= min_polls, f"{channel}: {counts[channel]} polled record(s), expected >= {min_polls}"


if __name__ == "__main__":
    sys.exit(run_pytest(__file__))
