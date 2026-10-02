"""NYC test rack: instro interface edge cases, exercised against whichever instruments discovery finds.

Covers the contract instro promises beyond the happy path: out-of-range setpoints are
rejected, device errors propagate and the instrument recovers, optional capabilities either
work or raise the documented "unsupported" exceptions, HAL guards, ``InstroPSU.apply`` ordering
and partial-failure semantics, published-record fidelity, and background polling racing
foreground commands on one session. Nothing assumes a particular model; wiring and safety are
described in rack_support.py, and nothing here exceeds 5 V / 1 A.

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
    OFF_THRESHOLD_V,
    SETTLE_S,
    UNSUPPORTED,
    Rack,
    assert_below,
    assert_close,
    assert_mode,
    latest,
    logger,
    optional,
    read_capture,
    run_pytest,
    wait_for_psu_readback,
)

from instro.dmm import InstroDMM
from instro.dmm.types import MeasurementFunction
from instro.eload import InstroELoad
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.types import Command, Measurement

pytestmark = pytest.mark.hardware

# Far outside any bench supply's range, so every driver or device must refuse them.
ABSURD_VOLTAGE_V = 1.0e4
ABSURD_CURRENT_A = 1.0e4
# Parks the bus channel somewhere harmless after a check that may have left a strange setpoint.
PARKED = {"current_limit": 0.1, "voltage": 1.0}


def _command_records(records: list[dict]) -> list[dict]:
    return [r for r in records if "timestamp" in r]


def _park_bus(rack: Rack) -> None:
    rack.psu.apply(current_limit=PARKED["current_limit"], voltage=PARKED["voltage"], channel=BUS_CH)


def test_psu_rejects_out_of_range_setpoints(rack: Rack, request: pytest.FixtureRequest) -> None:
    """Impossible setpoints raise (driver validation or a device error) and never publish a command."""
    psu = rack.psu
    _park_bus(rack)
    voltage_before = optional(lambda: latest(psu.get_voltage_setpoint(channel=BUS_CH)), "voltage setpoint readback")
    current_before = optional(lambda: latest(psu.get_current_setpoint(channel=BUS_CH)), "current setpoint readback")

    cases: list[tuple[str, Callable[[], object]]] = [
        ("negative voltage", lambda: psu.set_voltage(-1.0, channel=BUS_CH)),
        (f"{ABSURD_VOLTAGE_V:g} V", lambda: psu.set_voltage(ABSURD_VOLTAGE_V, channel=BUS_CH)),
        ("negative current limit", lambda: psu.set_current_limit(-1.0, channel=BUS_CH)),
        (f"{ABSURD_CURRENT_A:g} A current limit", lambda: psu.set_current_limit(ABSURD_CURRENT_A, channel=BUS_CH)),
    ]
    try:
        for label, call in cases:
            with pytest.raises((ValueError, RuntimeError)) as exc:
                call()
            logger.info("  PASS  rejected %-26s %s: %s", label, type(exc.value).__name__, exc.value)

        if voltage_before is not None:
            assert_close(
                "voltage setpoint unchanged", latest(psu.get_voltage_setpoint(channel=BUS_CH)), voltage_before, 0, 0.001
            )
        if current_before is not None:
            assert_close(
                "current setpoint unchanged", latest(psu.get_current_setpoint(channel=BUS_CH)), current_before, 0, 0.001
            )
        assert latest(psu.get_output_status(channel=BUS_CH)) == 0.0, "rejected setpoints must leave the output off"
    finally:
        _park_bus(rack)

    published = [
        (k, v)
        for r in _command_records(read_capture(rack.capture_path, request.node.name))
        for k, v in r["channel_data"].items()
        if k.endswith((".voltage.cmd", ".current.cmd")) and (v < 0 or v >= min(ABSURD_VOLTAGE_V, ABSURD_CURRENT_A))
    ]
    assert not published, f"rejected setpoints must not publish commands, got {published}"


def _probe(label: str, call: Callable[[], object], table: list[tuple[str, str]], failures: list[str]) -> None:
    """Record whether an optional call works, is unsupported, or breaks the contract by raising anything else."""
    try:
        result = call()
    except UNSUPPORTED as exc:
        table.append((label, f"unsupported ({type(exc).__name__})"))
    except Exception as exc:
        table.append((label, f"CONTRACT BREAK: {type(exc).__name__}: {exc}"))
        failures.append(f"{label}: {type(exc).__name__}: {exc}")
    else:
        value = result.latest if isinstance(result, Measurement) else ""
        table.append((label, f"supported {value!r}" if value != "" else "supported"))


def _log_table(title: str, table: list[tuple[str, str]]) -> None:
    logger.info("  %s:", title)
    for label, outcome in table:
        logger.info("    %-28s %s", label, outcome)


def test_psu_optional_capabilities_contract(rack: Rack) -> None:
    """Each optional PSU query either returns or raises NotImplementedError/FeatureNotSupportedError, never anything else."""
    psu = rack.psu
    ch = BUS_CH
    queries: list[tuple[str, Callable[[], object]]] = [
        ("voltage setpoint", lambda: psu.get_voltage_setpoint(channel=ch)),
        ("current setpoint", lambda: psu.get_current_setpoint(channel=ch)),
        ("operating mode", lambda: psu.get_operating_mode(channel=ch)),
        ("OVP level", lambda: psu.get_overvoltage_protection_level(channel=ch)),
        ("OVP enabled", lambda: psu.get_overvoltage_protection_enabled(channel=ch)),
        ("OVP delay", lambda: psu.get_overvoltage_protection_delay(channel=ch)),
        ("OVP tripped", lambda: psu.get_overvoltage_protection_tripped(channel=ch)),
        ("OCP level", lambda: psu.get_overcurrent_protection_level(channel=ch)),
        ("OCP enabled", lambda: psu.get_overcurrent_protection_enabled(channel=ch)),
        ("OCP tripped", lambda: psu.get_overcurrent_protection_tripped(channel=ch)),
        ("remote sense", lambda: psu.get_remote_sense_enabled(channel=ch)),
    ]
    table: list[tuple[str, str]] = []
    failures: list[str] = []
    for label, call in queries:
        _probe(label, call, table, failures)
    _log_table(f"PSU ({rack.found['psu'].driver_name}) CH{ch} optional queries", table)
    assert math.isfinite(latest(psu.get_voltage(channel=ch))), "PSU should still read after the probes"
    assert not failures, (
        "optional queries must raise NotImplementedError or FeatureNotSupportedError when unsupported: "
        + "; ".join(failures)
    )


def test_dmm_optional_capabilities_contract(rack: Rack) -> None:
    """Each optional DMM setting either applies or raises an "unsupported" exception, and the DMM still reads."""
    dmm = rack.dmm
    table: list[tuple[str, str]] = []
    failures: list[str] = []
    plan: list[tuple[MeasurementFunction, list[tuple[str, Callable[[], object]]]]] = [
        (
            MeasurementFunction.DC_VOLTAGE,
            [
                ("DCV manual range 10 V", lambda: dmm.set_range(10.0)),
                ("DCV auto range", lambda: dmm.set_range(None)),
                ("DCV NPLC 1", lambda: dmm.set_aperture_nplc(1)),
                ("DCV aperture 0.1 s", lambda: dmm.set_aperture_seconds(0.1)),
                ("DCV digits 6", lambda: dmm.set_digits(6)),
            ],
        ),
        (
            MeasurementFunction.DC_CURRENT,
            [("DCI manual range 0.1 A", lambda: dmm.set_range(0.1)), ("DCI NPLC 1", lambda: dmm.set_aperture_nplc(1))],
        ),
        (
            MeasurementFunction.AC_VOLTAGE,
            [("ACV auto range", lambda: dmm.set_range(None)), ("ACV NPLC 1", lambda: dmm.set_aperture_nplc(1))],
        ),
    ]
    for function, calls in plan:
        dmm.set_measurement_function(function)
        for label, call in calls:
            _probe(label, call, table, failures)
    _log_table(f"DMM ({rack.found['dmm'].driver_name}) optional settings", table)

    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    optional(lambda: dmm.set_range(None), "DCV auto range")
    optional(lambda: dmm.set_aperture_nplc(1), "DCV NPLC")
    assert math.isfinite(latest(dmm.read())), "DMM should still read after the probes"
    assert not failures, (
        "optional settings must raise NotImplementedError or FeatureNotSupportedError when unsupported: "
        + "; ".join(failures)
    )


ELOAD_MODE_RANGES = {LoadMode.CC: 3.0, LoadMode.CV: 20.0, LoadMode.CP: 10.0, LoadMode.CR: 100.0}


def test_eload_mode_and_range_contract(rack: Rack) -> None:
    """Every mode, its range, and the slew rate either apply or raise an "unsupported" exception (input off throughout)."""
    eload = rack.eload
    table: list[tuple[str, str]] = []
    failures: list[str] = []
    for mode, range_value in ELOAD_MODE_RANGES.items():
        before = len(failures)
        _probe(f"{mode.value} mode", lambda: eload.set_mode(mode), table, failures)
        if table[-1][1].startswith("supported") and len(failures) == before:
            _probe(f"{mode.value} range {range_value:g}", lambda: eload.set_range(range_value), table, failures)
    _probe("slew rate BOTH 0.1 A/µs", lambda: eload.set_slewrate(SlewRateDirection.BOTH, 0.1), table, failures)
    _log_table(f"eload ({rack.found['eload'].driver_name}) modes, ranges, slew", table)

    eload.set_mode(LoadMode.CC)
    eload.set_level(0.0)
    assert math.isfinite(latest(eload.get_voltage())), "eload should still read after the probes"
    assert not failures, (
        "unsupported modes/ranges must raise NotImplementedError or FeatureNotSupportedError: " + "; ".join(failures)
    )


ERROR_CASES: dict[str, Callable[[Rack], Measurement | None]] = {
    "psu": lambda r: r.psu.get_voltage(channel=BUS_CH),
    "dmm": lambda r: r.dmm.read_dc_voltage(),
    "eload": lambda r: r.eload.get_voltage(),
}


@pytest.mark.parametrize("role", ERROR_CASES)
def test_device_error_propagates_then_recovers(rack: Rack, role: str) -> None:
    """A queued SCPI error surfaces on the next HAL call as RuntimeError, then the instrument is usable again."""
    call = ERROR_CASES[role]
    # The suite's one deliberate step around the HAL: instro has no call that queues a device error.
    visa = getattr(getattr(rack, role)._driver, "_visa", None)
    if visa is None:
        pytest.skip(f"{role} driver isn't SCPI over VISA; no error queue to exercise")
    visa.write("BOGUS:COMMAND")
    # Every in-tree driver's _check_errors raises "<vendor/model> reported error: <device reply>".
    with pytest.raises(RuntimeError, match="reported error") as exc:
        call(rack)
    logger.info("  PASS  %s raised: %s", role, exc.value)
    value = latest(call(rack))
    logger.info("  PASS  %s recovered: next call returned %.4f", role, value)
    assert math.isfinite(value)


def test_hal_guards(instruments: dict[str, DiscoveredInstrument]) -> None:
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


def test_apply_disables_before_writing_setpoints(rack: Rack) -> None:
    """apply(enable=False) on a live output turns it off first, so the new setpoint never reaches the bus."""
    psu, dmm = rack.psu, rack.dmm
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=BUS_VOLTAGE_V, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, BUS_VOLTAGE_V, 0.005, 0.02)
    assert_close("DMM bus before apply", latest(dmm.read_dc_voltage()), BUS_VOLTAGE_V, 0.005, 0.02)

    commands = psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=2.0, channel=BUS_CH)
    order = [next(iter(c.channel_data)) for c in commands]
    logger.info("  apply(enable=False) published: %s", order)
    assert order == [f"psu.ch{BUS_CH}.enabled.cmd", f"psu.ch{BUS_CH}.current.cmd", f"psu.ch{BUS_CH}.voltage.cmd"]
    assert next(iter(commands[0].channel_data.values())) == 0.0, "first step must disable the output"
    assert latest(psu.get_output_status(channel=BUS_CH)) == 0.0, "output should be off after apply(enable=False)"
    assert_mode(psu, BUS_CH, "OFF", "apply(enable=False) disables the output first")
    if (
        setpoint := optional(lambda: latest(psu.get_voltage_setpoint(channel=BUS_CH)), "voltage setpoint readback")
    ) is not None:
        assert_close(f"CH{BUS_CH} voltage setpoint", setpoint, 2.0, 0, 0.001)

    time.sleep(SETTLE_S)
    assert_below("DMM bus: output off, not holding 2 V", latest(dmm.read_dc_voltage()), OFF_THRESHOLD_V)

    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=2.0, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, 2.0, 0.005, 0.02)
    assert_close("DMM bus after apply(enable=True)", latest(dmm.read_dc_voltage()), 2.0, 0.005, 0.02)


def test_apply_partial_failure_keeps_landed_steps(rack: Rack, request: pytest.FixtureRequest) -> None:
    """apply() isn't atomic: steps before a failing one stay committed and are still published."""
    psu = rack.psu
    ch = BUS_CH
    try:
        psu.apply(current_limit=0.5, voltage=1.0, channel=ch)
        with pytest.raises((ValueError, RuntimeError)) as exc:
            psu.apply(current_limit=0.2, voltage=ABSURD_VOLTAGE_V, channel=ch)
        logger.info("  voltage step failed as intended: %s: %s", type(exc.value).__name__, exc.value)

        if (
            limit := optional(lambda: latest(psu.get_current_setpoint(channel=ch)), "current setpoint readback")
        ) is not None:
            assert_close("current limit landed", limit, 0.2, 0, 0.001)
        if (
            setpoint := optional(lambda: latest(psu.get_voltage_setpoint(channel=ch)), "voltage setpoint readback")
        ) is not None:
            assert_close("voltage setpoint kept", setpoint, 1.0, 0, 0.001)
        assert latest(psu.get_output_status(channel=ch)) == 0.0

        published = [
            (k, v)
            for r in _command_records(read_capture(rack.capture_path, request.node.name))
            for k, v in r["channel_data"].items()
        ]
        logger.info("  published commands: %s", published)
        assert published == [
            (f"psu.ch{ch}.enabled.cmd", 0.0),
            (f"psu.ch{ch}.current.cmd", 0.5),
            (f"psu.ch{ch}.voltage.cmd", 1.0),
            (f"psu.ch{ch}.enabled.cmd", 0.0),
            (f"psu.ch{ch}.current.cmd", 0.2),
        ], "the failed apply() should publish exactly the two steps that landed"
    finally:
        _park_bus(rack)


def test_published_records_match_returned_objects(rack: Rack, request: pytest.FixtureRequest) -> None:
    """Every HAL call publishes exactly the object it returns: same type, channel, value, timestamp, and tags."""
    psu, dmm, eload = rack.psu, rack.dmm, rack.eload
    returned: list[Command | Measurement | None] = [
        dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE),
        psu.set_voltage(1.5, channel=BUS_CH),
        psu.set_current_limit(0.25, channel=BUS_CH),
        psu.get_voltage(channel=BUS_CH),
        psu.get_current(channel=BUS_CH),
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

    assert records[1]["channel_data"] == {f"psu.ch{BUS_CH}.voltage.cmd": 1.5}
    assert records[2]["channel_data"] == {f"psu.ch{BUS_CH}.current.cmd": 0.25}


def test_polling_during_foreground_commands(
    rack: Rack, request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture
) -> None:
    """The PSU daemon and the test thread share one session; neither may see the other's responses."""
    psu = rack.psu
    psu.apply(current_limit=BUS_CURRENT_LIMIT_A, voltage=1.0, enable=True, channel=BUS_CH)
    wait_for_psu_readback(psu, BUS_CH, 1.0, 0.005, 0.02)
    has_setpoint = optional(lambda: psu.get_voltage_setpoint(channel=BUS_CH), "voltage setpoint readback") is not None

    caplog.set_level(logging.ERROR, logger="instro.lib.instrument")
    psu.background_interval = 0.1
    steps = (1.0, 2.0, 3.0, 4.0) * 5
    start_ns = time.time_ns()
    psu.start()
    try:
        for voltage in steps:
            psu.set_voltage(voltage, channel=BUS_CH)
            if has_setpoint:
                setpoint = latest(psu.get_voltage_setpoint(channel=BUS_CH))
                assert abs(setpoint - voltage) <= 0.001, f"setpoint read {setpoint} right after writing {voltage}"
            assert latest(psu.get_output_status(channel=BUS_CH)) == 1.0
            assert math.isfinite(latest(psu.get_current(channel=BUS_CH)))
            time.sleep(0.05)
    finally:
        psu.stop()
    logger.info(
        "  PASS  %d foreground write/read sequences stayed consistent while polling%s",
        len(steps),
        "" if has_setpoint else " (no setpoint readback on this PSU; checked status and current)",
    )

    daemon_errors = [r.getMessage() for r in caplog.records if "Background daemon error" in r.getMessage()]
    assert not daemon_errors, f"background daemon raised: {daemon_errors}"

    polled: dict[str, list[float]] = {}
    for record in read_capture(rack.capture_path, request.node.name):
        if "timestamps" in record and record["timestamps"][0] >= start_ns:
            for channel, values in record["channel_data"].items():
                polled.setdefault(channel, []).extend(values)
    bus = polled.get(f"psu.ch{BUS_CH}.voltage", [])
    logger.info("  polled bus voltage: %d samples, %.3f-%.3f V", len(bus), min(bus, default=0), max(bus, default=0))
    assert len(bus) >= 5, f"daemon only polled CH{BUS_CH} voltage {len(bus)} time(s)"
    assert all(-0.05 <= v <= max(steps) + 0.05 for v in bus), f"polled bus voltage out of range: {bus}"
    assert all(v == 1.0 for v in polled.get(f"psu.ch{BUS_CH}.enabled", [])), "daemon saw the bus output off"


if __name__ == "__main__":
    sys.exit(run_pytest(__file__))
