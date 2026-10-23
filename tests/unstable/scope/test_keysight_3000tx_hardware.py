"""Hardware validation for Keysight InfiniiVision 3000T X-Series via InstroScope. Self-contained; no publishers.

Sweeps every method Keysight3000TX implements: per-channel setting roundtrips
(vertical scale/offset, coupling, probe attenuation, channel display), timebase,
sample-rate and status queries, acquisition mode and average count, run/stop/
single/digitize, waveform fetch, the eight built-in measurements, the trigger
surface, and host-side screenshot/settings I/O.

Designed to run with NOTHING connected to the inputs. Structural checks (set/get
roundtrips, finite values, expected lengths, a clean error queue) are always
asserted. Measurements that need edges (frequency, period, duty cycle) are
allowed to come back NaN; strict value checks run only when the matching
EXPECTED_* constant is set, which requires a stimulus on SIGNAL_CHANNEL.

Run (after setting VISA_RESOURCE below):
    uv run python tests/unstable/scope/test_keysight_3000tx_hardware.py

The scope is left running with its trigger in AUTO sweep.
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
from instro.unstable.scope.drivers import Keysight3000TX

# --- Configuration — edit before running -----------------------------------
VISA_RESOURCE = "TCPIP0::IP_ADDRESS::INSTR"  # <-- edit to your unit (LAN, or USB0::0x2A8D::...::INSTR)
NUM_CHANNELS = 4  # 2 for DSOX/MSOX30x2T models
SIGNAL_CHANNEL = 1  # channel used for trigger, digitize, waveform fetch, and measurements

# Stimulus expectations (None = skip the strict check; leave None with nothing wired).
EXPECTED_FREQUENCY_HZ: float | None = None
EXPECTED_VPP_V: float | None = None

# On-instrument file paths for save/recall (None = skip those checks). Needs a USB stick, e.g. "\\usb\\instro_hw".
INSTRUMENT_SETUP_PATH: str | None = None
INSTRUMENT_IMAGE_PATH: str | None = None

# Tolerances
REL_TOL = 0.05  # relative tolerance for snapped setting readbacks
FREQ_REL_TOL = 0.10
VPP_REL_TOL = 0.20
DIGITIZE_TIMEOUT_S = 10.0  # AUTO sweep auto-triggers in ~100 ms; generous for slow timebases
WAVEFORM_POINTS = 1000  # the driver requests 1000 points in NORMal mode

_EDGE_MEASUREMENTS = {ScopeMeasurementType.FREQUENCY, ScopeMeasurementType.PERIOD, ScopeMeasurementType.DUTY_CYCLE}


def _make_scope() -> tuple[InstroScope, Keysight3000TX]:
    driver = Keysight3000TX(VISA_RESOURCE)
    scope = InstroScope(name="hw_validate", driver=driver, num_channels=NUM_CHANNELS, publishers=None)
    scope.open()
    return scope, driver


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
    scope, driver = _make_scope()
    failures: list = []
    ch = SIGNAL_CHANNEL
    channels = range(1, NUM_CHANNELS + 1)
    try:
        # --- Connection / bulk getters + error drain ---
        def sync() -> None:
            cfg = scope.sync_configuration()
            assert set(cfg.channels) == set(channels), f"sync returned channels {sorted(cfg.channels)}"
            for c in channels:
                vs = cfg.channels[c].vertical_scale
                assert vs is not None and math.isfinite(vs) and vs > 0, f"CH{c} bad vertical scale: {vs}"
            driver.check_errors()

        _run("sync_configuration (all getters + check_errors)", sync, failures)

        # --- Channel display (driver extra; fetch/measure need the channel on) ---
        def display() -> None:
            for c in channels:
                driver.set_channel_display(True, channel=c)
                assert driver.get_channel_display(channel=c), f"CH{c} did not turn on"
            driver.check_errors()

        _run("channel display on (all channels)", display, failures)

        # --- Vertical scale roundtrip, every channel ---
        def vscale() -> None:
            for c in channels:
                for target in (1.0, 0.5):
                    scope.set_vertical_scale(target, channel=c)
                    got = scope.get_vertical_scale(channel=c).latest
                    assert math.isclose(got, target, rel_tol=REL_TOL), f"CH{c}: set {target} V/div, read {got}"
            driver.check_errors()

        _run("vertical scale set/get roundtrip (all channels)", vscale, failures)

        # --- Vertical offset roundtrip, every channel ---
        def voffset() -> None:
            for c in channels:
                scope.set_vertical_offset(0.25, channel=c)
                got = scope.get_vertical_offset(channel=c).latest
                assert math.isclose(got, 0.25, rel_tol=REL_TOL, abs_tol=0.01), f"CH{c}: set 0.25 V, read {got}"
                scope.set_vertical_offset(0.0, channel=c)
                assert abs(scope.get_vertical_offset(channel=c).latest) < 0.01, f"CH{c}: offset did not return to 0"
            driver.check_errors()

        _run("vertical offset set/get roundtrip (all channels)", voffset, failures)

        # --- Coupling roundtrip ---
        def coupling() -> None:
            for c in channels:
                scope.set_coupling(Coupling.AC, channel=c)
                assert scope.get_coupling(channel=c).latest == Coupling.AC.value, f"CH{c} did not read AC"
                scope.set_coupling(Coupling.DC, channel=c)
                assert scope.get_coupling(channel=c).latest == Coupling.DC.value, f"CH{c} did not read DC"
            driver.check_errors()

        _run("coupling set/get roundtrip (AC/DC, all channels)", coupling, failures)

        # --- Probe attenuation roundtrip (fails if an AutoProbe is attached; that is expected) ---
        def probe() -> None:
            for c in channels:
                scope.set_probe_attenuation(10, channel=c)
                got = scope.get_probe_attenuation(channel=c).latest
                assert math.isclose(got, 10, rel_tol=REL_TOL), f"CH{c}: set 10:1, read {got}"
                scope.set_probe_attenuation(1, channel=c)
                got = scope.get_probe_attenuation(channel=c).latest
                assert math.isclose(got, 1, rel_tol=REL_TOL), f"CH{c}: set 1:1, read {got}"
            driver.check_errors()

        _run("probe attenuation set/get roundtrip (all channels)", probe, failures)

        # --- Timebase roundtrip ---
        def timebase() -> None:
            for target in (1e-3, 1e-4):
                scope.set_horizontal_scale(target)
                got = scope.get_horizontal_scale().latest
                assert math.isclose(got, target, rel_tol=REL_TOL), f"set {target} s/div, read {got}"
            driver.check_errors()

        _run("horizontal scale set/get roundtrip", timebase, failures)

        # --- Sample rate query ---
        def sample_rate() -> None:
            sr = scope.get_sample_rate().latest
            assert math.isfinite(sr) and sr > 0, f"bad sample rate: {sr}"
            print(f"         sample rate: {sr:.3e} Sa/s")

        _run("sample rate query (:ACQuire:SRATe?)", sample_rate, failures)

        # --- Acquisition modes + average count ---
        def acq_modes() -> None:
            for mode in (
                AcquisitionMode.AVERAGE,
                AcquisitionMode.HIGH_RESOLUTION,
                AcquisitionMode.PEAK_DETECT,
                AcquisitionMode.NORMAL,
            ):
                scope.set_acquisition_mode(mode)
                got = scope.get_acquisition_mode().latest
                assert got == mode.value, f"set {mode.value}, read {got}"
            scope.set_acquisition_mode(AcquisitionMode.AVERAGE)
            scope.set_average_count(16)
            assert int(scope.get_average_count().latest) == 16, "average count did not read back 16"
            scope.set_acquisition_mode(AcquisitionMode.NORMAL)
            driver.check_errors()

        _run("acquisition mode roundtrip (AVER/HRES/PEAK/NORM) + average count", acq_modes, failures)

        def acq_envelope_unsupported() -> None:
            try:
                scope.set_acquisition_mode(AcquisitionMode.ENVELOPE)
            except NotImplementedError:
                return
            raise AssertionError("ENVELOPE mode should raise NotImplementedError on the 3000T X-Series")

        _run("acquisition mode ENVELOPE rejected", acq_envelope_unsupported, failures)

        # --- Trigger surface ---
        def trigger_config() -> None:
            scope.set_trigger_source(channel=ch)
            for ttype in (TriggerType.PULSE, TriggerType.EDGE):
                scope.set_trigger_type(ttype)
            for slope in (TriggerSlope.FALLING, TriggerSlope.EITHER, TriggerSlope.RISING):
                scope.set_trigger_slope(slope)
            scope.set_trigger_level(0.0)
            scope.set_trigger_mode(TriggerMode.NORMAL)
            scope.set_trigger_mode(TriggerMode.AUTO)
            driver.check_errors()

        _run("trigger source/type/slope/level/mode", trigger_config, failures)

        # --- run / state / stop ---
        def run_stop() -> None:
            scope.run()
            time.sleep(0.3)
            assert scope.get_acquisition_state().latest == "RUNNING"
            scope.stop_acquisition()
            time.sleep(0.2)
            assert scope.get_acquisition_state().latest == "STOPPED"
            driver.check_errors()

        _run("run -> RUNNING, stop -> STOPPED", run_stop, failures)

        # --- trigger status ---
        def trigger_status() -> None:
            scope.run()
            time.sleep(0.2)
            status = scope.get_trigger_status().latest
            assert status in {"ARMED", "AUTO", "TRIGGERED"}, f"unexpected trigger status: {status!r}"
            print(f"         trigger status while running: {status}")

        _run("get_trigger_status", trigger_status, failures)

        # --- force_trigger (NORMAL sweep so the force is what completes the single) ---
        def force() -> None:
            scope.set_trigger_mode(TriggerMode.NORMAL)
            scope.single()
            time.sleep(0.2)
            scope.force_trigger()
            time.sleep(0.5)
            assert scope.get_acquisition_state().latest == "STOPPED", "forced single did not stop the scope"
            scope.set_trigger_mode(TriggerMode.AUTO)
            driver.check_errors()

        _run("single + force_trigger", force, failures)

        # --- single + digitize via fetch_waveform (AUTO sweep auto-triggers with no signal) ---
        def digitize_fetch() -> None:
            scope.set_trigger_mode(TriggerMode.AUTO)
            scope.set_horizontal_scale(1e-4)
            scope.single()  # arms; fetch_waveform then drives driver.digitize()
            wf: Measurement = scope.fetch_waveform(channel=ch, timeout=DIGITIZE_TIMEOUT_S)
            volts = wf.values
            assert len(volts) == WAVEFORM_POINTS, f"expected {WAVEFORM_POINTS} points, got {len(volts)}"
            assert len(wf.timestamps) == len(volts), "timestamp/voltage length mismatch"
            assert all(math.isfinite(v) for v in volts), "non-finite sample in waveform"
            ts = list(wf.timestamps)
            assert all(b >= a for a, b in zip(ts, ts[1:], strict=False)), "timestamps not monotonic"
            assert ts[-1] > ts[0], "timestamps span zero time (x-increment lost?)"
            vpp = max(volts) - min(volts)
            print(f"         waveform: {len(volts)} pts, {ts[-1] - ts[0]} ns span, Vpp {vpp:.4f} V")
            if EXPECTED_VPP_V is not None:
                assert math.isclose(vpp, EXPECTED_VPP_V, rel_tol=VPP_REL_TOL), f"Vpp {vpp:.3f} vs {EXPECTED_VPP_V}"

        _run("single + digitize + fetch_waveform", digitize_fetch, failures)

        # --- Built-in measurements (edge measurements may be NaN with no signal) ---
        def measurements() -> None:
            scope.run()
            time.sleep(0.6)
            results: dict[ScopeMeasurementType, float] = {}
            for mtype in ScopeMeasurementType:
                val = scope.measure(mtype, channel=ch).latest
                results[mtype] = val
                assert isinstance(val, float), f"{mtype.value} returned {type(val).__name__}"
                if mtype not in _EDGE_MEASUREMENTS:
                    assert math.isfinite(val), f"{mtype.value} returned {val} on a displayed channel"
            driver.check_errors()
            summary = ", ".join(f"{m.value}={v:.4g}" for m, v in results.items())
            print(f"         {summary}")
            if EXPECTED_FREQUENCY_HZ is not None:
                freq = results[ScopeMeasurementType.FREQUENCY]
                assert math.isclose(freq, EXPECTED_FREQUENCY_HZ, rel_tol=FREQ_REL_TOL), (
                    f"frequency {freq:.1f} Hz vs expected {EXPECTED_FREQUENCY_HZ} Hz"
                )
            if EXPECTED_VPP_V is not None:
                vpp = results[ScopeMeasurementType.VPP]
                assert math.isclose(vpp, EXPECTED_VPP_V, rel_tol=VPP_REL_TOL), f"VPP {vpp:.3f} vs {EXPECTED_VPP_V}"

        _run("measure (VPP/VMAX/VMIN/VAVG/VRMS/FREQ/PERIOD/DUTY)", measurements, failures)

        # --- Screenshot (host transfer) ---
        def screenshot() -> None:
            path = os.path.join(tempfile.gettempdir(), "keysight_3000tx_hw_screenshot.png")
            scope.save_screenshot(path)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            assert size > 0, "screenshot file empty/missing"
            with open(path, "rb") as f:
                assert f.read(8) == b"\x89PNG\r\n\x1a\n", "screenshot is not a PNG"
            os.remove(path)
            driver.check_errors()
            print(f"         screenshot: {size} bytes")

        _run("save_screenshot (:DISPlay:DATA? PNG host transfer)", screenshot, failures)

        # --- Settings host roundtrip (:SYSTem:SETup? then :SYSTem:SETup) ---
        def settings_roundtrip() -> None:
            path = os.path.join(tempfile.gettempdir(), "keysight_3000tx_hw_setup.bin")
            scope.set_horizontal_scale(2e-4)
            scope.save_settings(path)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            assert size > 0, "settings file empty/missing"
            scope.set_horizontal_scale(1e-3)
            scope.load_settings(path)
            time.sleep(0.5)
            got = scope.sync_configuration().horizontal_scale
            assert got is not None and math.isclose(got, 2e-4, rel_tol=REL_TOL), (
                f"recall did not restore timebase: {got}"
            )
            os.remove(path)
            driver.check_errors()
            print(f"         learn string: {size} bytes")

        _run("save_settings + load_settings (host learn-string roundtrip)", settings_roundtrip, failures)

        # --- On-instrument save/recall (optional; needs a writable path on the scope) ---
        if INSTRUMENT_SETUP_PATH is not None:

            def settings_instrument() -> None:
                assert INSTRUMENT_SETUP_PATH is not None
                scope.save_settings(INSTRUMENT_SETUP_PATH, to_instrument=True)
                scope.load_settings(INSTRUMENT_SETUP_PATH, from_instrument=True)
                time.sleep(0.5)
                driver.check_errors()

            _run("save_settings/load_settings on instrument", settings_instrument, failures)
        else:
            _skip("save_settings/load_settings on instrument", "set INSTRUMENT_SETUP_PATH (USB stick) to enable")

        if INSTRUMENT_IMAGE_PATH is not None:

            def screenshot_instrument() -> None:
                assert INSTRUMENT_IMAGE_PATH is not None
                scope.save_screenshot(INSTRUMENT_IMAGE_PATH, to_instrument=True)
                driver.check_errors()

            _run("save_screenshot on instrument", screenshot_instrument, failures)
        else:
            _skip("save_screenshot on instrument", "set INSTRUMENT_IMAGE_PATH (USB stick) to enable")

        # --- Error queue must be clean at the end ---
        _run("error queue clean after sweep", driver.check_errors, failures)

    finally:
        try:
            scope.set_trigger_mode(TriggerMode.AUTO)
            scope.run()
        except Exception:  # noqa: BLE001 - best-effort safe state
            pass
        scope.close()
    return failures


@pytest.mark.hardware
def test_keysight_3000tx_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)} check(s))'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
