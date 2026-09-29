"""NYC test rack: instro interface edge cases, exercised against the real instruments.

Covers the contract instro promises beyond the happy path: driver-side range validation,
device-error propagation and recovery, unsupported-feature exceptions, HAL guards,
``InstroPSU.apply`` ordering and partial-failure semantics, published-record fidelity, and
background polling racing foreground commands on one VISA session. Wiring and safety are
described in rack_support.py; nothing here exceeds 5 V / 1 A.

Run:
    uv run python tests/rack/test_nyc_rack_interface.py
"""

from __future__ import annotations

import json
import logging
import math
import sys
import time
from collections.abc import Callable
from importlib.metadata import version

import pytest
from discovery import DiscoveredInstrument
from rack_support import (
    BUS_CH,
    BUS_CURRENT_LIMIT_A,
    BUS_VOLTAGE_V,
    ELOAD_CC_RANGE_A,
    OFF_THRESHOLD_V,
    OVP_V,
    SETTLE_S,
    SPARE_CH,
    Rack,
    assert_below,
    assert_close,
    latest,
    logger,
    read_capture,
    run_pytest,
    wait_for_psu_readback,
)

from instro.dmm import InstroDMM
from instro.dmm.types import MeasurementFunction
from instro.eload import InstroELoad
from instro.eload.types import LoadMode
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.types import Command, Measurement

pytestmark = pytest.mark.hardware


def _command_records(records: list[dict]) -> list[dict]:
    return [r for r in records if "timestamp" in r]


def test_psu_rejects_out_of_range_setpoints(rack: Rack, request: pytest.FixtureRequest) -> None:
    """RigolDP800 validates against the limits it queried on open, before anything reaches the wire."""
    psu = rack.psu
    limits = psu._driver._limits  # type: ignore[attr-defined]
    voltage_before = latest(psu.get_voltage_setpoint(channel=SPARE_CH))
    current_before = latest(psu.get_current_setpoint(channel=SPARE_CH))

    cases: list[tuple[str, Callable[[], object]]] = [
        (
            f"CH3 voltage above max ({limits[SPARE_CH].voltage.maximum} V)",
            lambda: psu.set_voltage(limits[SPARE_CH].voltage.maximum + 0.5, channel=SPARE_CH),
        ),
        ("CH3 negative voltage", lambda: psu.set_voltage(-1.0, channel=SPARE_CH)),
        (
            f"CH3 current above max ({limits[SPARE_CH].current.maximum} A)",
            lambda: psu.set_current_limit(limits[SPARE_CH].current.maximum + 0.5, channel=SPARE_CH),
        ),
        (
            f"CH1 OVP above max ({limits[BUS_CH].ovp.maximum} V)",
            lambda: psu.set_overvoltage_protection_level(limits[BUS_CH].ovp.maximum + 1.0, channel=BUS_CH),
        ),
    ]
    for label, call in cases:
        with pytest.raises(ValueError, match="out of range") as exc:
            call()
        logger.info("  PASS  rejected %-38s %s", label, exc.value)

    assert_close(
        "CH3 voltage setpoint unchanged", latest(psu.get_voltage_setpoint(channel=SPARE_CH)), voltage_before, 0, 0.001
    )
    assert_close(
        "CH3 current setpoint unchanged", latest(psu.get_current_setpoint(channel=SPARE_CH)), current_before, 0, 0.001
    )
    assert_close(
        "CH1 OVP unchanged", latest(psu.get_overvoltage_protection_level(channel=BUS_CH)), OVP_V[BUS_CH], 0, 0.01
    )

    published = {
        k for r in _command_records(read_capture(rack.capture_path, request.node.name)) for k in r["channel_data"]
    }
    assert not published, f"rejected setpoints must not publish commands, got {sorted(published)}"


ERROR_CASES: dict[str, Callable[[Rack], Measurement | None]] = {
    "psu": lambda r: r.psu.get_voltage(channel=BUS_CH),
    "dmm": lambda r: r.dmm.read_dc_voltage(),
    "eload": lambda r: r.eload.get_voltage(),
}


@pytest.mark.parametrize("role", ERROR_CASES)
def test_device_error_propagates_then_recovers(rack: Rack, role: str) -> None:
    """A queued SCPI error surfaces on the next HAL call as RuntimeError, then the instrument is usable again."""
    call = ERROR_CASES[role]
    getattr(rack, role)._driver._visa.write("BOGUS:COMMAND")
    # Every in-tree driver's _check_errors raises "<vendor/model> reported error: <device reply>".
    with pytest.raises(RuntimeError, match="reported error") as exc:
        call(rack)
    logger.info("  PASS  %s raised: %s", role, exc.value)
    value = latest(call(rack))
    logger.info("  PASS  %s recovered: next call returned %.4f", role, value)
    assert math.isfinite(value)


def test_unsupported_features_raise(rack: Rack) -> None:
    """Coverage gaps raise the documented exception types instead of sending commands the device can't honor."""
    psu, dmm = rack.psu, rack.dmm
    cases: list[tuple[str, type[Exception], Callable[[], object]]] = [
        # The DP832A has no sense terminals; the driver maps the device's NONE reply to this.
        ("PSU remote sense query", FeatureNotSupportedError, lambda: psu.get_remote_sense_enabled(channel=BUS_CH)),
        (
            "PSU OVP delay set",
            FeatureNotSupportedError,
            lambda: psu.set_overvoltage_protection_delay(0.1, channel=BUS_CH),
        ),
        ("PSU OVP delay query", FeatureNotSupportedError, lambda: psu.get_overvoltage_protection_delay(channel=BUS_CH)),
        # RigolDP800 doesn't implement get_operating_mode (mode is only in query_status); update when it does.
        ("PSU operating mode", NotImplementedError, lambda: psu.get_operating_mode(channel=BUS_CH)),
    ]
    for label, exc_type, call in cases:
        with pytest.raises(exc_type) as exc:
            call()
        logger.info("  PASS  %-24s -> %s: %s", label, type(exc.value).__name__, exc.value)

    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    with pytest.raises(NotImplementedError) as exc_digits:
        dmm.set_digits(6)
    logger.info("  PASS  %-24s -> %s", "DMM set_digits", exc_digits.value)
    dmm.set_measurement_function(MeasurementFunction.AC_VOLTAGE)
    with pytest.raises(NotImplementedError) as exc_nplc:
        dmm.set_aperture_nplc(1)
    logger.info("  PASS  %-24s -> %s", "DMM AC NPLC", exc_nplc.value)

    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    assert math.isfinite(latest(dmm.read())), "DMM should still read after rejected calls"
    assert math.isfinite(latest(psu.get_voltage(channel=BUS_CH))), "PSU should still read after rejected calls"


def test_hal_guards(rack: Rack, instruments: dict[str, DiscoveredInstrument]) -> None:
    """Guards that stop calls whose precondition isn't met; the fresh instruments are never opened."""
    fresh_eload = InstroELoad(name="guard_eload", driver=instruments["eload"].make_driver())
    fresh_dmm = InstroDMM(name="guard_dmm", driver=instruments["dmm"].make_driver())
    cases: list[tuple[str, type[Exception], Callable[[], object]]] = [
        ("eload set_level before set_mode", ValueError, lambda: fresh_eload.set_level(0.1)),
        ("eload set_range before set_mode", ValueError, lambda: fresh_eload.set_range(1.0)),
        ("DMM read before function", ValueError, lambda: fresh_dmm.read()),
        ("DMM start before function", ValueError, lambda: fresh_dmm.start()),
        ("DMM set_range before function", ValueError, lambda: fresh_dmm.set_range(10.0)),
        ("DMM set_digits before function", ValueError, lambda: fresh_dmm.set_digits(6)),
        ("DMM NPLC before function", ValueError, lambda: fresh_dmm.set_aperture_nplc(1)),
    ]
    for label, exc_type, call in cases:
        with pytest.raises(exc_type) as exc:
            call()
        logger.info("  PASS  %-32s -> %s: %s", label, type(exc.value).__name__, exc.value)

    eload = rack.eload
    eload.set_mode(LoadMode.CR)
    with pytest.raises(NotImplementedError) as exc_range:
        eload.set_range(10.0)
    logger.info("  PASS  %-32s -> %s", "eload set_range in CR", exc_range.value)
    eload.set_mode(LoadMode.CC)
    eload.set_range(ELOAD_CC_RANGE_A)


def test_apply_disables_before_writing_setpoints(rack: Rack) -> None:
    """apply(enable=False) on a live output turns it off first, so the new setpoint never reaches the bus."""
    psu, dmm = rack.psu, rack.dmm
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    assert_close("DMM bus before apply", latest(dmm.read_dc_voltage()), BUS_VOLTAGE_V, 0.005, 0.02)

    commands = psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=2.0, channel=BUS_CH)
    order = [next(iter(c.channel_data)) for c in commands]
    logger.info("  apply(enable=False) published: %s", order)
    assert order == ["psu.ch1.enabled.cmd", "psu.ch1.current.cmd", "psu.ch1.voltage.cmd"]
    assert next(iter(commands[0].channel_data.values())) == 0.0, "first step must disable the output"
    assert latest(psu.get_output_status(channel=BUS_CH)) == 0.0, "output should be off after apply(enable=False)"
    assert_close("CH1 voltage setpoint", latest(psu.get_voltage_setpoint(channel=BUS_CH)), 2.0, 0, 0.001)

    time.sleep(SETTLE_S)
    assert_below("DMM bus: output off, not holding 2 V", latest(dmm.read_dc_voltage()), OFF_THRESHOLD_V)

    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=2.0, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, 2.0, 0.005, 0.02)
    assert_close("DMM bus after apply(enable=True)", latest(dmm.read_dc_voltage()), 2.0, 0.005, 0.02)


def test_apply_partial_failure_keeps_landed_steps(rack: Rack, request: pytest.FixtureRequest) -> None:
    """apply() isn't atomic: steps before a failing one stay committed and are still published."""
    psu = rack.psu
    psu.apply(current_limit=0.5, voltage=1.0, channel=BUS_CH)
    bad_voltage = psu._driver._limits[BUS_CH].voltage.maximum + 1.0  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="out of range"):
        psu.apply(current_limit=0.2, voltage=bad_voltage, channel=BUS_CH)

    assert_close("current limit landed", latest(psu.get_current_setpoint(channel=BUS_CH)), 0.2, 0, 0.001)
    assert_close("voltage setpoint kept", latest(psu.get_voltage_setpoint(channel=BUS_CH)), 1.0, 0, 0.001)
    assert latest(psu.get_output_status(channel=BUS_CH)) == 0.0

    published = [
        (k, v)
        for r in _command_records(read_capture(rack.capture_path, request.node.name))
        for k, v in r["channel_data"].items()
    ]
    logger.info("  published commands: %s", published)
    assert published == [
        ("psu.ch1.enabled.cmd", 0.0),
        ("psu.ch1.current.cmd", 0.5),
        ("psu.ch1.voltage.cmd", 1.0),
        ("psu.ch1.enabled.cmd", 0.0),
        ("psu.ch1.current.cmd", 0.2),
    ], "the failed apply() should publish exactly the two steps that landed"


def test_published_records_match_returned_objects(rack: Rack, request: pytest.FixtureRequest) -> None:
    """Every HAL call publishes exactly the object it returns: same type, channel, value, timestamp, and tags."""
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    returned: list[Command | Measurement | None] = [
        dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE),
        psu.set_voltage(1.5, channel=BUS_CH),
        psu.set_current_limit(0.25, channel=BUS_CH),
        psu.get_voltage_setpoint(channel=BUS_CH),
        psu.get_current_setpoint(channel=BUS_CH),
        psu.get_output_status(channel=BUS_CH),
        dmm.read_dc_voltage(),
        eload.set_mode(LoadMode.CC),
        eload.get_voltage(),
        eload.get_current(),
    ]
    records = read_capture(rack.capture_path, request.node.name)
    assert len(records) == len(returned), f"expected {len(returned)} records, got {len(records)}"

    expected_tags = {
        "instro": version("instro"),
        "rack": "nyc",
        "run_id": rack.capture_path.parent.name,
        "suite": "interface",
        "check": request.node.name,
    }
    previous_ts = 0
    for obj, record in zip(returned, records):
        assert obj is not None
        assert record == json.loads(json.dumps(obj.__dict__)), f"record differs from returned object: {record}"
        channels = list(record["channel_data"])
        if isinstance(obj, Command):
            assert all(c.endswith(".cmd") for c in channels), f"command channel without .cmd: {channels}"
            ts = record["timestamp"]
        else:
            assert not any(c.endswith(".cmd") for c in channels), f"measurement channel with .cmd: {channels}"
            ts = record["timestamps"][0]
        assert record["tags"] == expected_tags, f"tags {record['tags']} != {expected_tags}"
        assert ts >= previous_ts, f"timestamps went backwards at {channels}"
        previous_ts = ts
        logger.info(
            "  PASS  %-10s %-32s %s", type(obj).__name__, channels[0], next(iter(record["channel_data"].values()))
        )

    assert_close("voltage setpoint round-trip", latest(returned[3]), 1.5, 0, 0.001)  # type: ignore[arg-type]
    assert_close("current setpoint round-trip", latest(returned[4]), 0.25, 0, 0.001)  # type: ignore[arg-type]


def test_polling_during_foreground_commands(
    rack: Rack, request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture
) -> None:
    """The PSU daemon and the test thread share one VISA session; neither may see the other's responses."""
    psu = rack.psu
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=1.0, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, 1.0, 0.005, 0.02)

    caplog.set_level(logging.ERROR, logger="instro.lib.instrument")
    psu.background_interval = 0.1
    steps = (1.0, 2.0, 3.0, 4.0) * 5
    start_ns = time.time_ns()
    psu.start()
    try:
        for voltage in steps:
            psu.set_voltage(voltage, channel=BUS_CH)
            setpoint = latest(psu.get_voltage_setpoint(channel=BUS_CH))
            assert abs(setpoint - voltage) <= 0.001, f"setpoint read {setpoint} right after writing {voltage}"
            assert latest(psu.get_output_status(channel=BUS_CH)) == 1.0
            time.sleep(0.05)
    finally:
        psu.stop()
    logger.info("  PASS  %d foreground set/readback pairs matched while polling", len(steps))

    daemon_errors = [r.getMessage() for r in caplog.records if "Background daemon error" in r.getMessage()]
    assert not daemon_errors, f"background daemon raised: {daemon_errors}"

    polled: dict[str, list[float]] = {}
    for record in read_capture(rack.capture_path, request.node.name):
        if "timestamps" in record and record["timestamps"][0] >= start_ns:
            for channel, values in record["channel_data"].items():
                polled.setdefault(channel, []).extend(values)
    bus = polled.get("psu.ch1.voltage", [])
    logger.info("  polled psu.ch1.voltage: %d samples, %.3f-%.3f V", len(bus), min(bus, default=0), max(bus, default=0))
    assert len(bus) >= 5, f"daemon only polled CH1 voltage {len(bus)} time(s)"
    assert all(-0.05 <= v <= max(steps) + 0.05 for v in bus), f"polled bus voltage out of range: {bus}"
    assert all(v == 1.0 for v in polled.get("psu.ch1.enabled", [])), "daemon saw CH1 output off"


if __name__ == "__main__":
    sys.exit(run_pytest(__file__))
