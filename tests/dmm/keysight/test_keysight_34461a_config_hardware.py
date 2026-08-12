"""Hardware validation of the JSON config-driven InstroDMM workflow on a Keysight 34461A.

Complements ``test_keysight_34461a_hardware.py``: that file validates the driver's
methods; this one validates that a JSON config builds a working ``InstroDMM`` against
the real instrument — driver/timing resolution from a config file, the ``measurement``
block actually programmed on ``open()``, config-driven background polling producing
measurements, and the ``autostart=True`` lifecycle. Schema-validation and constructor
rejection cases are unit-tested in ``tests/dmm/test_dmm_config.py`` and are not
repeated here.

Wiring / stimulus:
    Inputs OPEN (nothing connected). All reads are STRUCTURAL: each must parse to a
    finite float and raise no SCPI error.

Run:
    uv run python tests/dmm/keysight/test_keysight_34461a_config_hardware.py
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

from instro.dmm import InstroDMM
from instro.dmm.drivers import Keysight34461A
from instro.lib.types import Command, Measurement

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# e.g. "USB0::0x2A8D::0x1301::MY12345678::INSTR" or "TCPIP0::A-34461A-XXXXX.local::inst0::INSTR"
VISA_RESOURCE = "USB0::0x2A8D::0x1301::MY64031276::INSTR"  # <-- edit to your unit

POLL_SECONDS = 3.0
AUTOSTART_SECONDS = 2.0

CONFIG: dict[str, Any] = {
    "version": 1,
    "instrument": "InstroDMM",
    "device": {
        "name": "keysight_dmm_config_hw",
        "description": "Config-driven workflow hardware validation (open inputs)",
        "manufacturer": "Keysight",
        "model": "34461A",
    },
    "driver": {
        "name": "Keysight34461A",
        "connection_type": "visa",
        "visa": {"visa_resource": VISA_RESOURCE},
    },
    "measurement": {"function": "DC_VOLTAGE", "aperture_nplc": 10, "range": 10.0},
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


def _make_hal(capture: _CapturePublisher) -> InstroDMM:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "keysight_dmm_config.json"
        config_path.write_text(json.dumps(CONFIG))
        return InstroDMM(config=config_path, publishers=[capture])


def _check_resolution(hal: InstroDMM) -> None:
    assert hal.name == CONFIG["device"]["name"], f"name {hal.name!r}"
    assert isinstance(hal._driver, Keysight34461A), f"resolved driver {type(hal._driver).__name__!r}"
    interval = hal.background_interval
    assert interval == CONFIG["timing"]["poll_interval"], f"poll_interval {interval!r}"


def _check_measurement_applied(hal: InstroDMM) -> None:
    driver = hal._driver
    assert isinstance(driver, Keysight34461A)
    function = driver._visa.query("FUNC?").strip().strip('"')
    nplc = float(driver._visa.query("VOLT:DC:NPLC?"))
    vrange = float(driver._visa.query("VOLT:DC:RANG?"))
    print(f"         FUNC? -> {function}, NPLC -> {nplc:g}, range -> {vrange:g}")
    assert function == "VOLT", f"measurement function {function!r} != VOLT"
    assert nplc == pytest.approx(10), f"NPLC {nplc:g} != 10"
    assert vrange == pytest.approx(10.0), f"range {vrange:g} != 10"


def _read_value(hal: InstroDMM) -> None:
    value = hal.read().latest
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError(f"expected a numeric reading, got {value!r}")
    if not math.isfinite(float(value)):
        raise AssertionError(f"non-finite reading: {value}")
    print(f"         read -> {float(value):g}")


def _assert_polled(capture: _CapturePublisher, since: int, context: str) -> None:
    polled = capture.measurements[since:]
    assert len(polled) >= 2, f"{context}: expected >=2 polled measurements, got {len(polled)}"
    for measurement in polled:
        for channel, values in measurement.channel_data.items():
            assert values and all(math.isfinite(v) for v in values), f"{context}: non-finite values on {channel!r}"
    print(f"         {context}: {len(polled)} measurements published")


def _check_polling(hal: InstroDMM, capture: _CapturePublisher) -> None:
    before = len(capture.measurements)
    hal.start()
    time.sleep(POLL_SECONDS)
    hal.stop()
    _assert_polled(capture, before, f"{POLL_SECONDS:g}s at {CONFIG['timing']['poll_interval']}s interval")


def _check_autostart() -> None:
    capture = _CapturePublisher()
    hal = InstroDMM(config=CONFIG, autostart=True, publishers=[capture])
    try:
        time.sleep(AUTOSTART_SECONDS)
    finally:
        hal.close()
    _assert_polled(capture, 0, "autostart")


def run_all() -> list:
    capture = _CapturePublisher()
    hal = _make_hal(capture)
    failures: list = []
    _run("config file resolves name/driver/poll_interval", lambda: _check_resolution(hal), failures)
    hal.open()
    try:
        _run(
            "open() applied measurement block (DCV, NPLC 10, 10 V range)",
            lambda: _check_measurement_applied(hal),
            failures,
        )
        _run("read() DC voltage (open inputs, structural)", lambda: _read_value(hal), failures)
        _run("start()/stop() background polling publishes measurements", lambda: _check_polling(hal, capture), failures)
    finally:
        hal.close()
    _run("autostart=True end-to-end lifecycle", _check_autostart, failures)
    return failures


@pytest.mark.hardware
def test_keysight_34461a_config_hardware() -> None:
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
