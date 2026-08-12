"""Hardware validation of the JSON config-driven InstroPSU workflow on a Rigol DP800.

Complements ``test_rigol_dp800_hardware.py``: that file validates the driver's methods;
this one validates that a JSON config builds a working ``InstroPSU`` against the real
instrument — driver/timing resolution from a config file, setpoint programming and live
readback through the config-built HAL, config-driven background polling producing
measurements, and the ``autostart=True`` lifecycle. Schema-validation and constructor
rejection cases are unit-tested in ``tests/psu/test_psu_config.py`` and are not
repeated here.

Wiring / stimulus:
    Channel 1 terminals OPEN (no load). Output is enabled for ~1.5 s at 1 V with a
    0.1 A current limit; live current must read ~0 A.

Run:
    uv run python tests/psu/rigol/test_rigol_dp800_config_hardware.py
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

from instro.lib.types import Command, Measurement
from instro.psu import InstroPSU
from instro.psu.drivers import RigolDP800

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# e.g. "USB0::0x1AB1::0x0E11::DP8XXXXXXXXXX::INSTR" or "TCPIP0::<ip>::INSTR"
# Keep the programmed values comfortably inside the specific unit's ratings.
VISA_RESOURCE = "USB0::0x1AB1::0x0E11::DP8B26AM00234::INSTR"  # <-- edit to your unit

CHANNEL = 1
TEST_VOLTAGE = 1.0
TEST_CURRENT_LIMIT = 0.1
VOLTAGE_READBACK_TOLERANCE = 0.15
CURRENT_READBACK_TOLERANCE = 0.02
OUTPUT_SETTLE_SECONDS = 1.5
POLL_SECONDS = 3.0
AUTOSTART_SECONDS = 2.0

CONFIG: dict[str, Any] = {
    "version": 1,
    "instrument": "InstroPSU",
    "device": {
        "name": "rigol_psu_config_hw",
        "description": "Config-driven workflow hardware validation (no load)",
        "manufacturer": "Rigol",
        "model": "DP800",
    },
    "driver": {
        "name": "RigolDP800",
        "connection_type": "visa",
        "num_channels": 1,
        "visa": {"visa_resource": VISA_RESOURCE},
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


def _make_hal(capture: _CapturePublisher) -> InstroPSU:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "rigol_psu_config.json"
        config_path.write_text(json.dumps(CONFIG))
        return InstroPSU(config=config_path, publishers=[capture])


def _latest(measurement: Measurement | None) -> float:
    assert measurement is not None, "expected a Measurement, got None"
    value = measurement.latest
    assert isinstance(value, float), f"expected a float reading, got {value!r}"
    return value


def _check_resolution(psu: InstroPSU) -> None:
    assert psu.name == CONFIG["device"]["name"], f"name {psu.name!r}"
    assert isinstance(psu._driver, RigolDP800), f"resolved driver {type(psu._driver).__name__!r}"
    assert psu._num_channels == CONFIG["driver"]["num_channels"], f"num_channels {psu._num_channels!r}"
    interval = psu.background_interval
    assert interval == CONFIG["timing"]["poll_interval"], f"poll_interval {interval!r}"


def _check_setpoints(psu: InstroPSU) -> None:
    psu.set_voltage(TEST_VOLTAGE, channel=CHANNEL)
    psu.set_current_limit(TEST_CURRENT_LIMIT, channel=CHANNEL)
    voltage_setpoint = _latest(psu.get_voltage_setpoint(channel=CHANNEL))
    current_setpoint = _latest(psu.get_current_setpoint(channel=CHANNEL))
    print(f"         V_set -> {voltage_setpoint:g}, I_set -> {current_setpoint:g}")
    assert voltage_setpoint == pytest.approx(TEST_VOLTAGE), f"voltage setpoint {voltage_setpoint:g}"
    assert current_setpoint == pytest.approx(TEST_CURRENT_LIMIT), f"current setpoint {current_setpoint:g}"


def _check_output_on_readback(psu: InstroPSU) -> None:
    psu.output_enable(True, channel=CHANNEL)
    try:
        time.sleep(OUTPUT_SETTLE_SECONDS)
        status = _latest(psu.get_output_status(channel=CHANNEL))
        voltage = _latest(psu.get_voltage(channel=CHANNEL))
        current = _latest(psu.get_current(channel=CHANNEL))
    finally:
        psu.output_enable(False, channel=CHANNEL)
    print(f"         status -> {status!r}, V -> {voltage:g}, I -> {current:g}")
    assert status == 1.0, f"output status {status!r} != on"
    assert voltage == pytest.approx(TEST_VOLTAGE, abs=VOLTAGE_READBACK_TOLERANCE), f"live voltage {voltage:g}"
    assert current == pytest.approx(0.0, abs=CURRENT_READBACK_TOLERANCE), f"live current {current:g} not ~0 (no load)"


def _check_output_off(psu: InstroPSU) -> None:
    status = _latest(psu.get_output_status(channel=CHANNEL))
    assert status == 0.0, f"output status {status!r} != off"


def _assert_polled(capture: _CapturePublisher, since: int, context: str) -> None:
    polled = capture.measurements[since:]
    assert len(polled) >= 2, f"{context}: expected >=2 polled measurements, got {len(polled)}"
    for measurement in polled:
        for channel, values in measurement.channel_data.items():
            assert values and all(math.isfinite(v) for v in values), f"{context}: non-finite values on {channel!r}"
    print(f"         {context}: {len(polled)} measurements published")


def _check_polling(psu: InstroPSU, capture: _CapturePublisher) -> None:
    before = len(capture.measurements)
    psu.start()
    time.sleep(POLL_SECONDS)
    psu.stop()
    _assert_polled(capture, before, f"{POLL_SECONDS:g}s at {CONFIG['timing']['poll_interval']}s interval")


def _check_autostart() -> None:
    capture = _CapturePublisher()
    psu = InstroPSU(config=CONFIG, autostart=True, publishers=[capture])
    try:
        time.sleep(AUTOSTART_SECONDS)
    finally:
        psu.close()
    _assert_polled(capture, 0, "autostart")


def run_all() -> list:
    capture = _CapturePublisher()
    psu = _make_hal(capture)
    failures: list = []
    _run("config file resolves name/driver/num_channels/poll_interval", lambda: _check_resolution(psu), failures)
    psu.open()
    try:
        _run("set_voltage/set_current_limit + setpoint readback", lambda: _check_setpoints(psu), failures)
        _run("output on: status/voltage/current readback, then off", lambda: _check_output_on_readback(psu), failures)
        _run("output confirmed off", lambda: _check_output_off(psu), failures)
        _run("start()/stop() background polling publishes measurements", lambda: _check_polling(psu, capture), failures)
    finally:
        psu.output_enable(False, channel=CHANNEL)
        psu.close()
    _run("autostart=True end-to-end lifecycle", _check_autostart, failures)
    return failures


@pytest.mark.hardware
def test_rigol_dp800_config_hardware() -> None:
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
