"""Hardware validation for Tektronix MSO44B via InstroScope. Self-contained; no publishers.

Exercises every method the Tektronix4SeriesMSO scope and AFG views implement: setting roundtrips
(vertical scale/offset, coupling, probe attenuation, timebase), sample-rate and
status queries, acquisition control (run/stop/single/digitize), acquisition-mode
and average-count config, waveform fetch (including the trigger-referenced time
axis), the eight built-in measurements, the full trigger surface, and
screenshot/settings I/O both to the host and on the instrument.

Two check tiers: structural sanity (finite, positive, max > min, expected
length) is always asserted; strict value checks (measured frequency/amplitude,
probe-attenuation scaling, trigger-sample alignment) run only when the matching
EXPECTED_* constant is set.

Wiring / stimulus:
    The scope's built-in AFG output is looped back into CH1. The script
    configures the AFG (sine, EXPECTED_FREQUENCY_HZ, AFG_AMPLITUDE_VPP, 0 V
    offset, high-Z load) and enables its output, then restores the original AFG
    state. The whole instrument setup is snapshotted to C:/ before the run and
    recalled afterward.

Run:
    uv run python tests/scope/tektronix/test_tektronix_4series_hardware.py
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time
from collections.abc import Callable

import pytest
import pyvisa

from instro.awg import InstroAWG
from instro.awg.types import AmplitudeMeasurementUnit, Pulse, Sawtooth, Sine, Square, StaticValue, Triangle
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
from instro.scope.drivers import Tektronix4SeriesMSO

# --- Configuration — edit before running -----------------------------------
RESOURCE = "TCPIP::192.168.1.224::INSTR"  # <-- edit to your instrument's VISA resource string
NUM_CHANNELS = 4
SIGNAL_CHANNEL = 1  # AFG loops back into CH1

AFG_AMPLITUDE_VPP = 1.0

# Stimulus expectations (set to None to skip the corresponding strict check).
EXPECTED_FREQUENCY_HZ: float | None = 1000.0
EXPECTED_VPP_V: float | None = AFG_AMPLITUDE_VPP
EXPECTED_DUTY_PERCENT: float | None = 50.0  # the sine's mid-level crossings give a 50% duty cycle

# Tolerances
REL_TOL = 0.05  # relative tolerance for snapped setting readbacks
FREQ_REL_TOL = 0.05
VPP_REL_TOL = 0.10
ATTEN_RATIO_REL_TOL = 0.10
TRIGGER_ALIGN_ABS_V = 0.1  # sample nearest t=0 must sit within this of the trigger level
MIN_VPP_V = 0.1
DUTY_ABS_TOL = 5.0  # percent
FASTFRAME_TEST_FRAMES = 4
MEASURE_SCALE_S = 1e-3  # 10 ms window: PDUTY/PERIOD need two same-direction edges inside the acquisition

SNAPSHOT_PATH = "C:/nominal_hw_validate_orig.set"
ON_INSTRUMENT_SCREENSHOT = "C:/nominal_hw_validate_shot.png"
ON_INSTRUMENT_SETUP = "C:/nominal_hw_validate_saved.set"


class _Side:
    """Short-lived raw VISA session for AFG setup and file cleanup that the driver API doesn't cover."""

    def __init__(self) -> None:
        self._inst = pyvisa.ResourceManager().open_resource(RESOURCE, open_timeout=5000)
        self._inst.timeout = 30000

    def write(self, cmd: str) -> None:
        self._inst.write(cmd)

    def query(self, cmd: str) -> str:
        return str(self._inst.query(cmd)).strip()

    def opc(self) -> None:
        self.query("*OPC?")

    def close(self) -> None:
        self._inst.close()


_AFG_QUERIES = (
    "AFG:FUNCtion",
    "AFG:FREQuency",
    "AFG:AMPLitude",
    "AFG:OFFSet",
    "AFG:OUTPut:LOAd:IMPEDance",
    "AFG:OUTPut:MODe",
)


def _snapshot(side: _Side) -> tuple[bool, dict[str, str]]:
    afg = {q: side.query(f"{q}?") for q in _AFG_QUERIES}
    saved = False
    try:
        side.write("*CLS")  # ESR accumulates across sessions, so clear it before judging the save
        side.write(f'SAVe:SETUp "{SNAPSHOT_PATH}"')
        side.opc()
        saved = (int(float(side.query("*ESR?"))) & 0x3C) == 0
    except Exception as exc:  # noqa: BLE001 - snapshot is best-effort
        print(f"  [WARN] could not snapshot setup: {exc}")
    return saved, afg


def _restore(saved: bool, afg: dict[str, str]) -> None:
    side = _Side()
    try:
        if saved:
            side.write("ACQuire:STATE STOP")  # a recall is only reliable while the scope is not mid-acquisition
            side.write(f'RECAll:SETUp "{SNAPSHOT_PATH}"')
            side.opc()
            side.write(f'FILESystem:DELete "{SNAPSHOT_PATH}"')
            print("  restored setup from snapshot")
        else:
            for q, v in afg.items():
                side.write(f"{q} {v}")
            print("  restored AFG state only (no setup snapshot)")
        side.opc()
    finally:
        side.close()


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
    side = _Side()
    saved, afg = _snapshot(side)
    side.write(
        "HORizontal:FASTframe:STATE OFF"
    )  # the driver reads FastFrame but never configures it; start single-record
    side.close()

    device = Tektronix4SeriesMSO(RESOURCE)
    driver = device.scope
    scope = InstroScope(name="hw_validate", driver=driver, num_channels=NUM_CHANNELS, publishers=None)
    awg = InstroAWG(name="hw_validate_afg", driver=device.awg, num_channels=1, publishers=None)
    failures: list = []
    ch = SIGNAL_CHANNEL
    try:
        scope.open()
        awg.open()
        awg.set_output_load(1, None)
        awg.set_waveform(1, Sine(frequency_hz=EXPECTED_FREQUENCY_HZ or 1000.0))
        awg.set_amplitude(1, AFG_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)
        awg.set_offset(1, 0.0)
        awg.output_enable(1, True)

        # --- Connection / bulk getters + error drain ---
        def sync() -> None:
            cfg = scope.sync_configuration()
            assert cfg.channels, "sync_configuration returned no channel state"
            vs = cfg.channels[ch].vertical_scale
            assert vs is not None and math.isfinite(vs) and vs > 0, f"bad vertical scale: {vs}"

        _run("sync_configuration (all getters + check_errors)", sync, failures)

        def vscale() -> None:
            scope.set_vertical_scale(0.2, channel=ch)
            got = scope.get_vertical_scale(channel=ch).latest
            assert math.isclose(got, 0.2, rel_tol=REL_TOL), f"set 0.2 V/div, read {got}"

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
            scope.set_coupling(Coupling.DC, channel=ch)  # restore

        _run("coupling set/get roundtrip (AC/DC)", coupling, failures)

        def timebase() -> None:
            scope.set_horizontal_scale(2e-4)  # 200 us/div -> 2 cycles of 1 kHz across 10 divs
            got = scope.get_horizontal_scale().latest
            assert math.isclose(got, 2e-4, rel_tol=REL_TOL), f"set 200us/div, read {got}"

        _run("horizontal scale set/get roundtrip", timebase, failures)

        def sample_rate() -> None:
            sr = scope.get_sample_rate().latest
            assert math.isfinite(sr) and sr > 0, f"bad sample rate: {sr}"

        _run("sample rate query", sample_rate, failures)

        def acq_mode() -> None:
            for mode in (AcquisitionMode.NORMAL, AcquisitionMode.HIGH_RESOLUTION, AcquisitionMode.PEAK_DETECT):
                scope.set_acquisition_mode(mode)
                assert scope.get_acquisition_mode().latest == mode.value, f"mode {mode} did not round-trip"
            scope.set_acquisition_mode(AcquisitionMode.AVERAGE)
            assert scope.get_acquisition_mode().latest == AcquisitionMode.AVERAGE.value
            scope.set_average_count(16)
            assert int(scope.get_average_count().latest) == 16
            scope.set_acquisition_mode(AcquisitionMode.NORMAL)  # restore

        _run("acquisition mode + average count", acq_mode, failures)

        def trigger_config() -> None:
            scope.set_trigger_source(channel=ch)
            scope.set_trigger_type(TriggerType.EDGE)
            scope.set_trigger_slope(TriggerSlope.RISING)
            scope.set_trigger_level(0.0)
            scope.set_trigger_mode(TriggerMode.AUTO)

        _run("trigger source/type/slope/level/mode", trigger_config, failures)

        def trigger_type_pulse() -> None:
            scope.set_trigger_type(TriggerType.PULSE)
            scope.set_trigger_type(TriggerType.EDGE)  # restore

        _run("trigger type PULSE accepted", trigger_type_pulse, failures)

        def run_stop() -> None:
            scope.run()
            time.sleep(0.3)
            assert scope.get_acquisition_state().latest == "RUNNING"
            scope.stop_acquisition()
            time.sleep(0.2)
            assert scope.get_acquisition_state().latest == "STOPPED"

        _run("run -> RUNNING, stop -> STOPPED", run_stop, failures)

        def digitize_fetch() -> None:
            scope.run()
            time.sleep(0.5)
            scope.set_trigger_level(0.0)
            scope.set_trigger_slope(TriggerSlope.RISING)
            scope.single()
            wf: Measurement = scope.fetch_waveform(channel=ch, timeout=3.0)
            volts = wf.values
            assert len(volts) > 100, f"short waveform: {len(volts)} pts"
            assert all(math.isfinite(v) for v in volts), "non-finite sample in waveform"
            assert len(wf.timestamps) == len(volts), "timestamp/voltage length mismatch"
            vpp = max(volts) - min(volts)
            assert vpp > MIN_VPP_V, f"waveform Vpp {vpp:.3f} below {MIN_VPP_V} V — is CH1 driven?"

        _run("single + digitize + fetch_waveform", digitize_fetch, failures)

        def full_record() -> None:
            scope.run()
            time.sleep(0.5)
            scope.stop_acquisition()
            record = int(float(driver._visa.query("HORizontal:RECOrdlength?")))  # noqa: SLF001
            driver._visa.write("DATa:STOP 10")  # noqa: SLF001 - plant a stale window
            wf = driver.fetch_waveform(ch)
            assert len(wf.voltages) == record, f"fetched {len(wf.voltages)} of {record} points with stale DATa:STOP"

        _run("fetch_waveform returns the full record despite stale DATa:STOP", full_record, failures)

        def fastframe_burst_is_read_as_one_timeline() -> None:
            frames = FASTFRAME_TEST_FRAMES
            # The driver reads FastFrame but never configures it, so set it the way a user would: on the instrument.
            cfg = _Side()
            try:
                cfg.write("HORizontal:POSition 50")  # the centers below assume the trigger sits mid-record
                cfg.write("HORizontal:FASTframe:STATE ON")
                cfg.write(f"HORizontal:FASTframe:COUNt {frames}")
                cfg.opc()
            finally:
                cfg.close()
            try:
                record = int(float(driver._visa.query("HORizontal:RECOrdlength?")))  # noqa: SLF001
                scope.set_trigger_mode(TriggerMode.NORMAL)
                scope.set_trigger_level(0.0)
                scope.set_trigger_slope(TriggerSlope.RISING)
                driver.digitize(10.0)
                wf = driver.fetch_waveform(ch)
            finally:
                cfg = _Side()
                try:
                    cfg.write("HORizontal:FASTframe:STATE OFF")
                    cfg.opc()
                finally:
                    cfg.close()
                scope.set_trigger_mode(TriggerMode.AUTO)  # restore
            assert len(wf.voltages) == frames * record, f"got {len(wf.voltages)} points, expected {frames * record}"
            centers = [f * record + record // 2 for f in range(frames)]  # trigger sample sits mid-record
            assert abs(wf.times[centers[-1]]) < 1_000, f"last frame trigger at {wf.times[centers[-1]]} ns, expected ~0"
            for f, c in enumerate(centers):
                assert abs(wf.voltages[c]) < TRIGGER_ALIGN_ABS_V, f"frame {f}: {wf.voltages[c]:.3f} V at its trigger"
            period_ns = 1e9 / (EXPECTED_FREQUENCY_HZ or 1000.0)
            for a, b in zip(centers, centers[1:]):
                gap = wf.times[b] - wf.times[a]
                cycles = round(gap / period_ns)
                assert cycles >= 1 and abs(gap - cycles * period_ns) < 5_000, (
                    f"frame gap {gap} ns is not a whole number of periods"
                )
            assert all(wf.times[i] < wf.times[i + 1] for i in range(len(wf.times) - 1)), (
                "time axis not strictly increasing"
            )

        _run(
            "FastFrame burst set on the instrument is read as one timeline",
            fastframe_burst_is_read_as_one_timeline,
            failures,
        )

        def roll_mode_fetch_anchors_the_newest_sample() -> None:
            scope.set_horizontal_scale(0.4)  # 4 s window: the scope is in roll mode
            scope.run()
            time.sleep(6)
            assert driver._visa.query("HORizontal:ROLL?").strip() == "1", "expected roll mode at 0.4 s/div"
            wf = driver.fetch_waveform(ch)
            scope.set_horizontal_scale(2e-4)  # restore
            assert wf.times[-1] == 0, f"newest sample at {wf.times[-1]} ns, expected 0"
            window_s = (wf.times[-1] - wf.times[0]) / 1e9
            assert 3.9 < window_s < 4.1, f"window {window_s:.2f} s, expected about 4 s"

        _run("roll-mode fetch anchors the newest sample at t = 0", roll_mode_fetch_anchors_the_newest_sample, failures)

        def time_axis() -> None:
            scope.set_trigger_mode(TriggerMode.NORMAL)
            scope.set_trigger_level(0.0)
            scope.set_trigger_slope(TriggerSlope.RISING)
            scope.run()
            time.sleep(0.6)
            scope.stop_acquisition()
            wf = driver.fetch_waveform(ch)
            times = wf.times
            assert times[0] < 0 < times[-1], f"time axis not trigger-referenced: {times[0]}..{times[-1]} ns"
            i0 = min(range(len(times)), key=lambda i: abs(times[i]))
            assert abs(wf.voltages[i0]) < TRIGGER_ALIGN_ABS_V, (
                f"sample nearest t=0 is {wf.voltages[i0]:.3f} V, expected ~0 V trigger level (PT_Off convention?)"
            )
            window_ns = 100_000  # ~10% of a 1 kHz period: a rising sine moves several hundred mV across it
            before = [v for t, v in zip(times, wf.voltages) if -window_ns <= t < 0]
            after = [v for t, v in zip(times, wf.voltages) if 0 < t <= window_ns]
            assert before and after, "no samples within 100 us of the trigger"
            mean_before, mean_after = sum(before) / len(before), sum(after) / len(after)
            assert mean_after - mean_before > 0.1, (
                f"rising trigger but mean voltage did not rise across t=0 ({mean_before:.3f} -> {mean_after:.3f})"
            )
            scope.set_trigger_mode(TriggerMode.AUTO)  # restore

        _run("time axis is trigger-referenced (PT_Off convention)", time_axis, failures)

        def probe_attenuation() -> None:
            scope.run()
            time.sleep(0.5)
            scope.set_probe_attenuation(1, channel=ch)
            assert math.isclose(scope.get_probe_attenuation(channel=ch).latest, 1, rel_tol=REL_TOL)
            time.sleep(0.5)
            vpp1 = scope.measure(ScopeMeasurementType.VPP, channel=ch).latest
            scope.set_probe_attenuation(10, channel=ch)
            got = scope.get_probe_attenuation(channel=ch).latest
            raw = driver._visa.query(f"CH{ch}:PROBEFunc:EXTAtten?")  # noqa: SLF001
            print(f"         probe attenuation 10 -> get {got}, raw EXTAtten {raw}")
            scope.set_vertical_scale(2.0, channel=ch)  # displayed amplitude is 10x; avoid clipping
            assert math.isclose(got, 10, rel_tol=REL_TOL), f"set 10, read {got}"
            time.sleep(0.5)
            vpp10 = driver.measure(ScopeMeasurementType.VPP, ch)  # noqa: SLF001
            print(f"         VPP at 1x: {vpp1:.4f} V, at 10x: {vpp10:.4f} V")
            scope.set_probe_attenuation(1, channel=ch)  # restore
            scope.set_vertical_scale(0.2, channel=ch)
            assert math.isclose(vpp10 / vpp1, 10, rel_tol=ATTEN_RATIO_REL_TOL), (
                f"10x attenuation should scale readings 10x; got ratio {vpp10 / vpp1:.3f} "
                f"(inverted semantics would give ~0.1)"
            )

        _run("probe attenuation roundtrip and scaling direction", probe_attenuation, failures)

        def measurements() -> None:
            scope.set_horizontal_scale(MEASURE_SCALE_S)
            scope.run()
            time.sleep(1.0)
            results: dict[ScopeMeasurementType, float] = {}
            for mtype in ScopeMeasurementType:
                val = scope.measure(mtype, channel=ch).latest
                results[mtype] = val
                assert not math.isnan(val), f"{mtype.value} returned NaN"
            assert results[ScopeMeasurementType.VPP] > MIN_VPP_V, f"VPP {results[ScopeMeasurementType.VPP]}"
            if EXPECTED_FREQUENCY_HZ is not None:
                freq = results[ScopeMeasurementType.FREQUENCY]
                assert math.isclose(freq, EXPECTED_FREQUENCY_HZ, rel_tol=FREQ_REL_TOL), (
                    f"frequency {freq:.1f} Hz vs expected {EXPECTED_FREQUENCY_HZ} Hz"
                )
            if EXPECTED_VPP_V is not None:
                vpp = results[ScopeMeasurementType.VPP]
                assert math.isclose(vpp, EXPECTED_VPP_V, rel_tol=VPP_REL_TOL), (
                    f"Vpp {vpp:.3f} V vs expected {EXPECTED_VPP_V} V"
                )
            if EXPECTED_DUTY_PERCENT is not None:
                duty = results[ScopeMeasurementType.DUTY_CYCLE]
                assert abs(duty - EXPECTED_DUTY_PERCENT) < DUTY_ABS_TOL, (
                    f"duty {duty:.1f}% vs expected {EXPECTED_DUTY_PERCENT}%"
                )
            scope.set_horizontal_scale(2e-4)  # restore

        _run("measure (VPP/VMAX/VMIN/VAVG/VRMS/FREQ/PERIOD/DUTY)", measurements, failures)

        def force() -> None:
            scope.set_trigger_mode(TriggerMode.NORMAL)
            scope.single()
            scope.force_trigger()
            scope.set_trigger_mode(TriggerMode.AUTO)  # restore

        _run("force_trigger", force, failures)

        def trigger_status() -> None:
            scope.run()
            time.sleep(0.2)
            status = scope.get_trigger_status().latest
            assert isinstance(status, str) and status, f"bad trigger status: {status!r}"

        _run("get_trigger_status", trigger_status, failures)

        def screenshot() -> None:
            path = os.path.join(tempfile.gettempdir(), "mso44b_hw_screenshot.png")
            scope.save_screenshot(path)
            assert os.path.exists(path) and os.path.getsize(path) > 0, "screenshot file empty/missing"
            with open(path, "rb") as f:
                assert f.read(8) == b"\x89PNG\r\n\x1a\n", "screenshot is not a PNG (READFile framing?)"
            os.remove(path)

        _run("save_screenshot (host transfer)", screenshot, failures)

        def screenshot_on_instrument() -> None:
            scope.save_screenshot(ON_INSTRUMENT_SCREENSHOT, to_instrument=True)
            cleanup = _Side()
            try:
                cleanup.opc()
                names = cleanup.query("FILESystem:DIR?")
                assert "nominal_hw_validate_shot" in names, f"screenshot not found on instrument: {names}"
                cleanup.write(f'FILESystem:DELete "{ON_INSTRUMENT_SCREENSHOT}"')
                cleanup.opc()
            finally:
                cleanup.close()

        _run("save_screenshot (to instrument)", screenshot_on_instrument, failures)

        def settings_roundtrip_host() -> None:
            path = os.path.join(tempfile.gettempdir(), "mso44b_hw_setup.set")
            scope.set_vertical_scale(0.5, channel=ch)
            scope.save_settings(path)
            assert os.path.exists(path) and os.path.getsize(path) > 0, "settings file empty/missing"
            scope.set_vertical_scale(0.1, channel=ch)
            scope.load_settings(path)
            time.sleep(0.5)
            got = scope.get_vertical_scale(channel=ch).latest
            os.remove(path)
            assert math.isclose(got, 0.5, rel_tol=REL_TOL), f"after host load, vertical scale {got} (expected 0.5)"
            scope.set_vertical_scale(0.2, channel=ch)  # restore

        _run("save_settings / load_settings (host round trip, WRITEFile framing)", settings_roundtrip_host, failures)

        def settings_roundtrip_instrument() -> None:
            scope.set_vertical_scale(0.5, channel=ch)
            scope.save_settings(ON_INSTRUMENT_SETUP, to_instrument=True)
            scope.set_vertical_scale(0.1, channel=ch)
            scope.load_settings(ON_INSTRUMENT_SETUP, from_instrument=True)
            time.sleep(0.5)
            got = scope.get_vertical_scale(channel=ch).latest
            cleanup = _Side()
            try:
                cleanup.write(f'FILESystem:DELete "{ON_INSTRUMENT_SETUP}"')
                cleanup.opc()
            finally:
                cleanup.close()
            assert math.isclose(got, 0.5, rel_tol=REL_TOL), (
                f"after instrument load, vertical scale {got} (expected 0.5)"
            )
            scope.set_vertical_scale(0.2, channel=ch)  # restore

        _run("save_settings / load_settings (on-instrument round trip)", settings_roundtrip_instrument, failures)

        # --- AFG view (InstroAWG) ---
        def _close(a: object, b: object) -> bool:
            if type(a) is not type(b):
                return False
            return all(math.isclose(getattr(a, f), getattr(b, f), rel_tol=REL_TOL, abs_tol=1e-9) for f in vars(a))

        def afg_waveforms() -> None:
            freq = EXPECTED_FREQUENCY_HZ or 1000.0
            for wf in (
                Sine(frequency_hz=freq),
                Square(frequency_hz=freq, duty_cycle_pct=25.0),
                Sawtooth(frequency_hz=freq),
                Triangle(frequency_hz=freq),
                Pulse(frequency_hz=freq, width_s=1e-4),
                StaticValue(value=0.5),
            ):
                awg.set_waveform(1, wf)
                got = awg.get_waveform(1)
                assert _close(got, wf), f"set {wf}, read {got}"
            awg.set_waveform(1, Sine(frequency_hz=freq))  # restore

        _run("AFG waveform set/get roundtrip (sine/square/sawtooth/triangle/pulse/DC)", afg_waveforms, failures)

        def afg_amplitude_offset() -> None:
            awg.set_amplitude(1, 0.8, AmplitudeMeasurementUnit.VPP)
            amp, unit = awg.get_amplitude(1)
            assert unit is AmplitudeMeasurementUnit.VPP and math.isclose(amp, 0.8, rel_tol=REL_TOL), f"{amp} {unit}"
            awg.set_offset(1, 0.1)
            assert math.isclose(awg.get_offset(1).latest, 0.1, abs_tol=0.01)
            awg.set_amplitude(1, AFG_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)  # restore
            awg.set_offset(1, 0.0)

        _run("AFG amplitude/offset set/get roundtrip", afg_amplitude_offset, failures)

        def afg_load() -> None:
            awg.set_output_load(1, 50)
            got50 = awg.get_output_load(1).latest
            assert got50 == 50.0, f"set 50 ohm, read {got50}"
            awg.set_output_load(1, None)
            gotz = awg.get_output_load(1).latest
            assert math.isinf(gotz), f"set high-Z, read {gotz} (HAL reports high-Z as inf)"

        _run("AFG output load set/get roundtrip (50 ohm / high-Z)", afg_load, failures)

        def afg_output_state() -> None:
            scope.set_vertical_scale(0.2, channel=ch)
            scope.run()
            try:
                awg.output_enable(1, False)
                assert awg.get_output_state(1).latest == 0.0, "output should read disabled"
                time.sleep(0.8)
                off_vpp = driver.measure(ScopeMeasurementType.VPP, ch)
                awg.output_enable(1, True)
                assert awg.get_output_state(1).latest == 1.0, "output should read enabled"
                time.sleep(0.8)
                on_vpp = driver.measure(ScopeMeasurementType.VPP, ch)
            finally:
                awg.output_enable(1, True)
            assert off_vpp < 0.1 * on_vpp, f"output disabled but CH1 Vpp {off_vpp:.3f} V vs {on_vpp:.3f} V enabled"

        _run("AFG output enable/disable observed on CH1", afg_output_state, failures)

        def afg_square_duty() -> None:
            freq = EXPECTED_FREQUENCY_HZ or 1000.0
            awg.set_waveform(1, Square(frequency_hz=freq, duty_cycle_pct=25.0))
            scope.set_horizontal_scale(MEASURE_SCALE_S)
            scope.run()
            time.sleep(1.0)
            duty = scope.measure(ScopeMeasurementType.DUTY_CYCLE, channel=ch).latest
            scope.set_horizontal_scale(2e-4)  # restore
            awg.set_waveform(1, Sine(frequency_hz=freq))  # restore
            assert abs(duty - 25.0) < DUTY_ABS_TOL, f"AFG square duty 25% but DUTY_CYCLE measured {duty:.1f}%"

        _run("AFG square duty observed via DUTY_CYCLE measurement", afg_square_duty, failures)

        def _rising_fraction(volts: list[float]) -> float:
            k = len(volts) // 50  # 2% of the record, so the step scales with record length
            diffs = [volts[i + k] - volts[i] for i in range(0, len(volts) - k, max(len(volts) // 500, 1))]
            return sum(d > 0.01 for d in diffs) / len(diffs)

        def afg_ramp_shapes() -> None:
            freq = EXPECTED_FREQUENCY_HZ or 1000.0
            fractions = {}
            for wf in (Sawtooth(frequency_hz=freq), Triangle(frequency_hz=freq)):
                awg.set_waveform(1, wf)
                scope.run()
                time.sleep(0.8)
                scope.stop_acquisition()
                fractions[type(wf).__name__] = _rising_fraction(driver.fetch_waveform(ch).voltages)
            print(f"         rising fraction: {fractions}")
            assert fractions["Sawtooth"] > 0.85, f"sawtooth should mostly rise; got {fractions['Sawtooth']:.2f}"
            assert 0.4 < fractions["Triangle"] < 0.6, (
                f"triangle should rise half the time; got {fractions['Triangle']:.2f}"
            )
            awg.set_waveform(1, Sine(frequency_hz=freq))  # restore

        _run("AFG sawtooth/triangle shapes observed on CH1", afg_ramp_shapes, failures)

    finally:
        try:
            scope.stop_acquisition()
        except Exception:  # noqa: BLE001 - best-effort safe state
            pass
        try:
            awg.output_enable(1, False)
        except Exception:  # noqa: BLE001 - best-effort safe state
            pass
        scope.close()
        awg.close()
        try:
            _restore(saved, afg)
        except Exception as exc:  # noqa: BLE001 - report, don't mask the run result
            print(f"  [WARN] restore failed, instrument left in test state: {exc}")
    return failures


@pytest.mark.hardware
def test_mso44b_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)} check(s))'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
