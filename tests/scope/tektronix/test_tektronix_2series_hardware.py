"""Hardware validation for Tektronix 2 Series MSO via InstroScope. Self-contained; no publishers.

Exercises every public method Tektronix2SeriesMSO implements: setting roundtrips
(vertical scale/offset, coupling, probe attenuation, timebase), sample-rate and
status queries, acquisition control (run/stop/single/digitize), acquisition-mode
and average-count config, waveform fetch (including a regression for the
``DATa:STOP`` truncation bug), the eight built-in measurements, the trigger
surface, and screenshot/settings I/O.

Two check tiers: structural sanity (finite, expected length) is always asserted;
strict value checks (measured frequency/duty) run only when the matching
EXPECTED_* constant is set.

Wiring / stimulus:
    None required (STIMULUS_ON_SIGNAL_CHANNEL=False forces the trigger). With nothing connected, signal-dependent measurements may
    legitimately return NaN and are only checked for being floats. Set
    EXPECTED_* after feeding a known signal into SIGNAL_CHANNEL.

Run:
    uv run python tests/scope/tektronix/test_tektronix_2series_hardware.py
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time
from collections.abc import Callable

import pytest

from instro.lib.types import Measurement
from instro.scope import (
    AcquisitionMode,
    Coupling,
    InstroScope,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerType,
)
from instro.scope.drivers import Tektronix2SeriesMSO

# --- Configuration — edit before running -----------------------------------
RESOURCE = "<visa_resource>"  # <-- edit to your instrument's VISA resource string
NUM_CHANNELS = 4
SIGNAL_CHANNEL = 1

STIMULUS_ON_SIGNAL_CHANNEL = False  # True if a signal crossing the trigger level is wired to SIGNAL_CHANNEL

# Stimulus expectations (None skips the strict check).
EXPECTED_FREQUENCY_HZ: float | None = None
EXPECTED_DUTY_PERCENT: float | None = None

# Tolerances
REL_TOL = 0.05
FREQ_REL_TOL = 0.10
DUTY_ABS_TOL = 10.0
STALE_STOP = 500  # a short DATa:STOP window left by a previous transfer


def _make_scope(driver: Tektronix2SeriesMSO) -> InstroScope:
    scope = InstroScope(
        name="hw_validate",
        driver=driver,
        num_channels=NUM_CHANNELS,
        publishers=None,
    )
    scope.open()
    return scope


def _run(name: str, fn: Callable[[], None], failures: list) -> None:
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def _skip(name: str, reason: str) -> None:
    print(f"  [SKIP] {name}: {reason}")


def run_all() -> list:
    driver = Tektronix2SeriesMSO(RESOURCE)
    scope = _make_scope(driver)
    visa = driver._visa  # raw access for record-length truth and file cleanup
    failures: list = []
    ch = SIGNAL_CHANNEL
    try:
        # --- Connection / bulk getters + error drain ---
        def sync() -> None:
            cfg = scope.sync_configuration()
            assert cfg.channels, "sync_configuration returned no channel state"
            vs = cfg.channels[ch].vertical_scale
            assert vs is not None and math.isfinite(vs) and vs > 0, f"bad vertical scale: {vs}"

        _run("sync_configuration (all getters + check_errors)", sync, failures)

        def errors() -> None:
            driver.check_errors()

        _run("check_errors", errors, failures)

        # --- Vertical ---
        def vscale() -> None:
            scope.set_vertical_scale(1.0, channel=ch)
            got = scope.get_vertical_scale(channel=ch).latest
            assert math.isclose(got, 1.0, rel_tol=REL_TOL), f"set 1.0 V/div, read {got}"

        _run("vertical scale set/get roundtrip", vscale, failures)

        def voffset() -> None:
            scope.set_vertical_offset(0.0, channel=ch)
            got = scope.get_vertical_offset(channel=ch).latest
            assert abs(got) < 0.05, f"set 0 V offset, read {got}"

        _run("vertical offset set/get roundtrip", voffset, failures)

        def coupling() -> None:
            scope.set_coupling(Coupling.DC, channel=ch)
            assert scope.get_coupling(channel=ch).latest == Coupling.DC.value
            scope.set_coupling(Coupling.AC, channel=ch)
            assert scope.get_coupling(channel=ch).latest == Coupling.AC.value
            scope.set_coupling(Coupling.DC, channel=ch)

        _run("coupling set/get roundtrip (AC/DC)", coupling, failures)

        def probe() -> None:
            scope.set_probe_attenuation(10, channel=ch)
            assert math.isclose(scope.get_probe_attenuation(channel=ch).latest, 10, rel_tol=REL_TOL)
            scope.set_probe_attenuation(1, channel=ch)
            assert math.isclose(scope.get_probe_attenuation(channel=ch).latest, 1, rel_tol=REL_TOL)

        _run("probe attenuation set/get roundtrip", probe, failures)

        # --- Horizontal ---
        def timebase() -> None:
            scope.set_horizontal_scale(2e-4)
            got = scope.get_horizontal_scale().latest
            assert math.isclose(got, 2e-4, rel_tol=REL_TOL), f"set 200us/div, read {got}"

        _run("horizontal scale set/get roundtrip", timebase, failures)

        def sample_rate() -> None:
            sr = scope.get_sample_rate().latest
            assert math.isfinite(sr) and sr > 0, f"bad sample rate: {sr}"

        _run("sample rate query", sample_rate, failures)

        # --- Acquisition mode + average count ---
        def acq_mode() -> None:
            scope.set_acquisition_mode(AcquisitionMode.NORMAL)
            assert scope.get_acquisition_mode().latest == AcquisitionMode.NORMAL.value
            scope.set_acquisition_mode(AcquisitionMode.AVERAGE)
            assert scope.get_acquisition_mode().latest == AcquisitionMode.AVERAGE.value
            scope.set_average_count(16)
            assert int(scope.get_average_count().latest) == 16
            scope.set_acquisition_mode(AcquisitionMode.HIGH_RESOLUTION)
            assert scope.get_acquisition_mode().latest == AcquisitionMode.HIGH_RESOLUTION.value
            scope.set_acquisition_mode(AcquisitionMode.NORMAL)

        _run("acquisition mode + average count", acq_mode, failures)

        # --- Trigger configuration ---
        def trigger_config() -> None:
            scope.set_trigger_source(channel=ch)
            scope.set_trigger_type(TriggerType.EDGE)
            scope.set_trigger_slope(TriggerSlope.RISING)
            scope.set_trigger_level(0.0)
            scope.set_trigger_mode(TriggerMode.AUTO)
            driver.check_errors()

        _run("trigger source/type/slope/level/mode", trigger_config, failures)

        # --- run / state / stop ---
        def run_stop() -> None:
            scope.run()
            time.sleep(0.5)
            assert scope.get_acquisition_state().latest == "RUNNING"
            scope.stop_acquisition()
            time.sleep(0.3)
            assert scope.get_acquisition_state().latest == "STOPPED"

        _run("run -> RUNNING, stop -> STOPPED", run_stop, failures)

        # --- single + digitize via fetch_waveform ---
        def digitize_fetch() -> None:
            scope.set_trigger_mode(TriggerMode.AUTO)
            scope.single()
            if not STIMULUS_ON_SIGNAL_CHANNEL:
                # Single-sequence ignores AUTO and waits for a real trigger edge. Polling the state to STOPPED
                # also disarms the HAL, so fetch_waveform reads this capture instead of re-digitizing.
                scope.force_trigger()
                deadline = time.monotonic() + 5.0
                while scope.get_acquisition_state().latest != "STOPPED":
                    assert time.monotonic() < deadline, "forced trigger did not complete the acquisition"
                    time.sleep(0.05)
            wf: Measurement = scope.fetch_waveform(channel=ch, timeout=5.0)
            volts = wf.values
            assert len(volts) > 100, f"short waveform: {len(volts)} pts"
            assert all(math.isfinite(float(v)) for v in volts), "non-finite sample in waveform"
            assert len(wf.timestamps) == len(volts), "timestamp/voltage length mismatch"

        _run("single + digitize + fetch_waveform", digitize_fetch, failures)

        # --- Regression: full record despite a stale DATa:STOP window ---
        def fetch_full_record() -> None:
            scope.stop_acquisition()
            record_length = int(float(visa.query("HORizontal:RECOrdlength?")))
            assert record_length > STALE_STOP
            visa.write("DATa:STARt 1")
            visa.write(f"DATa:STOP {STALE_STOP}")
            wf = scope.fetch_waveform(channel=ch, timeout=5.0)
            assert len(wf.values) == record_length, f"got {len(wf.values)} of {record_length} points"
            assert len(wf.timestamps) == record_length

        _run("fetch_waveform returns full record after short DATa:STOP", fetch_full_record, failures)

        # --- Built-in measurements ---
        def measurements() -> None:
            scope.run()
            time.sleep(0.8)
            results: dict[ScopeMeasurementType, float] = {}
            for mtype in ScopeMeasurementType:
                val = scope.measure(mtype, channel=ch).latest
                assert isinstance(val, float), f"{mtype.value} returned {type(val)}"
                results[mtype] = val  # NaN is legal with no stimulus
            if EXPECTED_FREQUENCY_HZ is not None:
                freq = results[ScopeMeasurementType.FREQUENCY]
                assert math.isclose(freq, EXPECTED_FREQUENCY_HZ, rel_tol=FREQ_REL_TOL), (
                    f"frequency {freq:.1f} Hz vs expected {EXPECTED_FREQUENCY_HZ} Hz"
                )
            if EXPECTED_DUTY_PERCENT is not None:
                duty = results[ScopeMeasurementType.DUTY_CYCLE]
                assert abs(duty - EXPECTED_DUTY_PERCENT) < DUTY_ABS_TOL, (
                    f"duty {duty:.1f}% vs expected {EXPECTED_DUTY_PERCENT}%"
                )

        _run("measure (VPP/VMAX/VMIN/VAVG/VRMS/FREQ/PERIOD/DUTY)", measurements, failures)

        def clear_measurements() -> None:
            driver.clear_measurements()
            assert not driver._measurement_slots

        _run("clear_measurements", clear_measurements, failures)

        # --- force_trigger / trigger status ---
        def force() -> None:
            scope.set_trigger_mode(TriggerMode.NORMAL)
            scope.single()
            scope.force_trigger()
            scope.set_trigger_mode(TriggerMode.AUTO)
            driver.check_errors()

        _run("force_trigger", force, failures)

        def trigger_status() -> None:
            scope.run()
            time.sleep(0.3)
            status = scope.get_trigger_status().latest
            assert isinstance(status, str) and status, f"bad trigger status: {status!r}"

        _run("get_trigger_status", trigger_status, failures)

        # --- Screenshot ---
        def screenshot() -> None:
            path = os.path.join(tempfile.gettempdir(), "mso2_hw_screenshot.png")
            scope.save_screenshot(path)
            with open(path, "rb") as f:
                data = f.read()
            os.remove(path)
            assert data.startswith(b"\x89PNG\r\n\x1a\n") and data.rstrip().endswith(b"IEND\xaeB`\x82"), (
                f"incomplete PNG ({len(data)} bytes)"
            )
            driver.check_errors()
            assert "TEKTRONIX" in visa.query("*IDN?")  # session still in sync after the binary read

        _run("save_screenshot (host transfer)", screenshot, failures)

        def screenshot_on_instrument() -> None:
            remote = "C:/nominal_hw_test.png"
            scope.save_screenshot(remote, to_instrument=True)
            visa.query("*OPC?")
            visa.write(f'FILESystem:DELete "{remote}"')
            driver.check_errors()

        _run("save_screenshot (to instrument)", screenshot_on_instrument, failures)

        # --- Settings ---
        def settings_roundtrip() -> None:
            path = os.path.join(tempfile.gettempdir(), "mso2_hw_setup.set")
            scope.set_vertical_scale(0.5, channel=ch)
            scope.save_settings(path)
            assert os.path.exists(path) and os.path.getsize(path) > 0, "settings file empty/missing"
            scope.set_vertical_scale(2.0, channel=ch)
            scope.load_settings(path)
            visa.query("*OPC?")
            got = scope.get_vertical_scale(channel=ch).latest
            assert math.isclose(got, 0.5, rel_tol=REL_TOL), f"after load_settings expected 0.5 V/div, read {got}"
            os.remove(path)

        _run("save_settings + load_settings (host) roundtrip", settings_roundtrip, failures)

        def settings_on_instrument() -> None:
            remote = "C:/nominal_hw_test.set"
            scope.set_vertical_scale(0.5, channel=ch)
            scope.save_settings(remote, to_instrument=True)
            visa.query("*OPC?")
            scope.set_vertical_scale(2.0, channel=ch)
            scope.load_settings(remote, from_instrument=True)
            visa.query("*OPC?")
            got = scope.get_vertical_scale(channel=ch).latest
            visa.write(f'FILESystem:DELete "{remote}"')
            assert math.isclose(got, 0.5, rel_tol=REL_TOL), f"after recall expected 0.5 V/div, read {got}"

        _run("save_settings/load_settings (on instrument) roundtrip", settings_on_instrument, failures)

    finally:
        try:
            scope.stop_acquisition()
        except Exception:  # noqa: BLE001 - best-effort safe state
            pass
        scope.close()
    return failures


@pytest.mark.hardware
def test_tektronix_2series_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)} check(s))'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
