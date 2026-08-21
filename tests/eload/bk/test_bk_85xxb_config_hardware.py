"""Hardware validation of the JSON config-driven InstroELoad workflow on a B&K 8514B.

Complements ``test_bk_85xxb_hardware.py``: that file validates the driver's methods;
this one validates that a JSON config builds a working ``InstroELoad`` against the
real instrument — driver/timing resolution from a config file, the ``load`` block
actually programmed on ``open()`` (CV mode/level/range/slew plus ``curr_limit``,
which BK85XXB arms as the CURR:PROT over-current trip) with the input left
DISABLED, config-driven background polling producing measurements, and the
``autostart=True`` lifecycle. Schema-validation and constructor rejection cases are
unit-tested in ``tests/eload/test_eload_config.py`` and are not repeated here.

Wiring / stimulus:
    Load INPUT terminals OPEN (nothing connected). The config never enables the
    input, so no current is drawn; readbacks are STRUCTURAL (finite, ~0).

Run:
    uv run python tests/eload/bk/test_bk_85xxb_config_hardware.py
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

from instro.eload import InstroELoad
from instro.eload.drivers.bk_85xxb import BK85XXB
from instro.eload.types import LoadMode
from instro.lib.types import Command, Measurement

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# e.g. "ASRL8::INSTR" for RS-232. Keep the programmed values comfortably inside
# the specific unit's ratings (8514B: 120 V / 240 A / 1500 W).
VISA_RESOURCE = "ASRL8::INSTR"  # <-- edit to your unit
BAUD_RATE = 9600

CHANNEL = 1
CV_LEVEL_V = 5.0
CURR_LIMIT_A = 0.5
CV_RANGE_V = 10.0
SLEW_RATE_A_PER_US = 0.1
POLL_SECONDS = 3.0
AUTOSTART_SECONDS = 2.0

CONFIG: dict[str, Any] = {
    "version": 1,
    "instrument": "InstroELoad",
    "device": {
        "name": "bk_eload_config_hw",
        "description": "Config-driven workflow hardware validation (open terminals)",
        "manufacturer": "B&K Precision",
        "model": "8514B",
    },
    "driver": {
        "name": "BK85XXB",
        "connection_type": "visa",
        "visa": {
            "visa_resource": VISA_RESOURCE,
            "serial_config": {"baud_rate": BAUD_RATE},
        },
    },
    "load": {
        "mode": "CV",
        "level": CV_LEVEL_V,
        "curr_limit": CURR_LIMIT_A,
        "range": CV_RANGE_V,
        "slew_rate": {"direction": "BOTH", "rate": SLEW_RATE_A_PER_US},
    },
    "timing": {"poll_interval": 0.5},
}


class _CapturePublisher:
    """In-memory publisher: records measurements so polling can be asserted without file output."""

    def __init__(self) -> None:
        self.measurements: list[Measurement] = []

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        if isinstance(data, Measurement):
            self.measurements.append(data)

    def close(self) -> None:
        pass


def _run(name, fn, failures) -> None:
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def _make_hal(capture: _CapturePublisher) -> InstroELoad:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "bk_eload_config.json"
        config_path.write_text(json.dumps(CONFIG))
        return InstroELoad(config=config_path, publishers=[capture])


def _check_resolution(eload: InstroELoad) -> None:
    assert eload.name == CONFIG["device"]["name"], f"name {eload.name!r}"
    assert isinstance(eload._driver, BK85XXB), f"resolved driver {type(eload._driver).__name__!r}"
    interval = eload.background_interval
    assert interval == CONFIG["timing"]["poll_interval"], f"poll_interval {interval!r}"


def _check_load_applied(eload: InstroELoad) -> None:
    driver = eload._driver
    assert isinstance(driver, BK85XXB)
    function = driver._visa.query("FUNCtion?").strip().upper()
    level = float(driver._visa.query("VOLTage?"))
    vrange = float(driver._visa.query("VOLTage:RANGe?"))
    prot = float(driver._visa.query("CURRent:PROTection?"))
    print(f"         FUNC? -> {function}, level -> {level:g} V, range -> {vrange:g} V, CURR:PROT? -> {prot:g} A")
    assert function.startswith("VOLT"), f"mode {function!r} != CV"
    assert eload._mode is LoadMode.CV, f"HAL mode cache {eload._mode!r}"
    assert level == pytest.approx(CV_LEVEL_V), f"CV level {level:g}"
    assert vrange >= CV_RANGE_V, f"range {vrange:g} does not cover configured {CV_RANGE_V:g}"
    assert prot == pytest.approx(CURR_LIMIT_A), f"protection level {prot:g} != configured curr_limit"


def _check_input_disabled(eload: InstroELoad) -> None:
    driver = eload._driver
    assert isinstance(driver, BK85XXB)
    input_state = driver._visa.query("INPut?").strip()
    print(f"         INPut? -> {input_state!r}")
    assert input_state.startswith("0") or input_state.upper() == "OFF", f"input enabled by config: {input_state!r}"


def _latest(measurement: Measurement | None) -> float:
    assert measurement is not None, "expected a Measurement, got None"
    value = measurement.latest
    assert isinstance(value, float), f"expected a float reading, got {value!r}"
    return value


def _check_readback(eload: InstroELoad) -> None:
    current = _latest(eload.get_current(channel=CHANNEL))
    voltage = _latest(eload.get_voltage(channel=CHANNEL))
    print(f"         I = {current} A, V = {voltage} V")
    assert math.isfinite(current), f"non-finite current: {current}"
    assert math.isfinite(voltage), f"non-finite voltage: {voltage}"


def _assert_polled(capture: _CapturePublisher, since: int, context: str) -> None:
    polled = capture.measurements[since:]
    assert len(polled) >= 2, f"{context}: expected >=2 polled measurements, got {len(polled)}"
    for measurement in polled:
        for channel, values in measurement.channel_data.items():
            assert values and all(math.isfinite(v) for v in values), f"{context}: non-finite values on {channel!r}"
    print(f"         {context}: {len(polled)} measurements published")


def _check_reopen_reapplies(eload: InstroELoad) -> None:
    eload.close()
    eload.open()
    driver = eload._driver
    assert isinstance(driver, BK85XXB)
    function = driver._visa.query("FUNCtion?").strip().upper()
    prot = float(driver._visa.query("CURRent:PROTection?"))
    print(f"         after reopen: FUNC? -> {function}, CURR:PROT? -> {prot:g} A")
    assert function.startswith("VOLT"), f"mode {function!r} not re-applied on reopen"
    assert prot == pytest.approx(CURR_LIMIT_A), f"curr_limit {prot:g} not re-applied on reopen"


def _check_polling(eload: InstroELoad, capture: _CapturePublisher) -> None:
    before = len(capture.measurements)
    eload.start()
    time.sleep(POLL_SECONDS)
    eload.stop()
    _assert_polled(capture, before, f"{POLL_SECONDS:g}s at {CONFIG['timing']['poll_interval']}s interval")


def _check_autostart() -> None:
    capture = _CapturePublisher()
    eload = InstroELoad(config=CONFIG, autostart=True, publishers=[capture])
    try:
        time.sleep(AUTOSTART_SECONDS)
    finally:
        eload.close()
    _assert_polled(capture, 0, "autostart")


def run_all() -> list:
    capture = _CapturePublisher()
    eload = _make_hal(capture)
    failures: list = []
    _run("config file resolves name/driver/poll_interval", lambda: _check_resolution(eload), failures)
    eload.open()
    try:
        _run("open() applied load block (CV, level, range, curr_limit)", lambda: _check_load_applied(eload), failures)
        _run("input left disabled by config", lambda: _check_input_disabled(eload), failures)
        _run("get_current/get_voltage (open terminals, structural)", lambda: _check_readback(eload), failures)
        _run(
            "start()/stop() background polling publishes measurements", lambda: _check_polling(eload, capture), failures
        )
        _run("close()/open() re-applies configured load block", lambda: _check_reopen_reapplies(eload), failures)
    finally:
        try:
            eload.output_enable(False, channel=CHANNEL)
        except Exception:  # noqa: BLE001 - best-effort safe state
            pass
        eload.close()
    _run("autostart=True end-to-end lifecycle", _check_autostart, failures)
    return failures


@pytest.mark.hardware
def test_bk_85xxb_config_hardware() -> None:
    if VISA_RESOURCE == "FILL_ME_IN":
        pytest.skip("VISA_RESOURCE is not set")
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    if VISA_RESOURCE == "FILL_ME_IN":
        print("Edit VISA_RESOURCE at the top of this file before running.")
        return 1
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
