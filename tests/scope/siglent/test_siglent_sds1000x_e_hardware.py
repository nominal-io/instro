"""Hardware validation for Siglent SDS1104X-E via InstroScope. Self-contained; no publishers.

Exercises every method SiglentSDS1000XE implements: setting roundtrips
(vertical scale/offset, coupling, probe attenuation, timebase), sample-rate and
status queries, acquisition control (run/stop/single/digitize),
acquisition-mode and average-count config, waveform fetch, the eight built-in
measurements, the full trigger surface, and host-side screenshot/settings I/O.

Two check tiers: structural sanity (finite, positive, max > min, expected
length) is always asserted; strict value checks (e.g. measured frequency ≈ the
comp signal's 1 kHz) run only when the matching EXPECTED_* constant is set.

Wiring / stimulus:
    The scope's built-in probe-compensation square wave (~1 kHz, ~3 Vpp) is fed
    into CH1. CH1 is the active/trigger channel. No external gear required.

Run:
    uv run python tests/scope/siglent/test_siglent_sds1000x_e_hardware.py
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time
from collections.abc import Callable

import pytest

from instro.lib.types import Command, Measurement
from instro.unstable.scope import (
    AcquisitionMode,
    Coupling,
    InstroScope,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerType,
)
from instro.unstable.scope.drivers.siglent import SiglentSDS1000XE

# --- Configuration — edit before running -----------------------------------
RESOURCE = "USB0::0xF4EC::0xEE38::SDSMMGKD804634::INSTR"
NUM_CHANNELS = 4
SIGNAL_CHANNEL = 1  # CH1 carries the ~1 kHz comp-signal square wave

# Stimulus expectations (set to None to skip the corresponding strict check).
EXPECTED_FREQUENCY_HZ: float | None = 1000.0  # comp signal default is 1 kHz
EXPECTED_DUTY_PERCENT: float | None = 50.0  # square wave ≈ 50% duty

# Tolerances
REL_TOL = 0.05  # relative tolerance for snapped setting readbacks
FREQ_REL_TOL = 0.10
DUTY_ABS_TOL = 10.0  # percent
MIN_VPP_V = 0.1  # the comp signal must show at least this much amplitude


def _cmd_value(cmd: Command) -> float | str:
    """Unwrap the single value a HAL command-getter packages."""
    return next(iter(cmd.channel_data.values()))


def _make_scope() -> InstroScope:
    scope = InstroScope(
        name="hw_validate",
        driver=SiglentSDS1000XE(RESOURCE),
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
    scope = _make_scope()
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

        # --- Vertical scale roundtrip ---
        def vscale() -> None:
            scope.set_vertical_scale(1.0, channel=ch)
            got = scope.get_vertical_scale(channel=ch).latest
            assert math.isclose(got, 1.0, rel_tol=REL_TOL), f"set 1.0 V/div, read {got}"

        _run("vertical scale set/get roundtrip", vscale, failures)

        # --- Vertical offset roundtrip ---
        def voffset() -> None:
            scope.set_vertical_offset(0.0, channel=ch)
            got = scope.get_vertical_offset(channel=ch).latest
            assert abs(got) < 0.05, f"set 0 V offset, read {got}"

        _run("vertical offset set/get roundtrip", voffset, failures)

        # --- Coupling roundtrip ---
        def coupling() -> None:
            scope.set_coupling(Coupling.DC, channel=ch)
            assert _cmd_value(scope.get_coupling(channel=ch)) == Coupling.DC.value
            scope.set_coupling(Coupling.AC, channel=ch)
            assert _cmd_value(scope.get_coupling(channel=ch)) == Coupling.AC.value
            scope.set_coupling(Coupling.DC, channel=ch)  # restore

        _run("coupling set/get roundtrip (AC/DC)", coupling, failures)

        # --- Probe attenuation roundtrip ---
        def probe() -> None:
            scope.set_probe_attenuation(10, channel=ch)
            assert math.isclose(scope.get_probe_attenuation(channel=ch).latest, 10, rel_tol=REL_TOL)
            scope.set_probe_attenuation(1, channel=ch)  # restore to match the direct BNC feed
            assert math.isclose(scope.get_probe_attenuation(channel=ch).latest, 1, rel_tol=REL_TOL)

        _run("probe attenuation set/get roundtrip", probe, failures)

        # --- Timebase roundtrip ---
        def timebase() -> None:
            scope.set_horizontal_scale(2e-4)  # 200 us/div -> ~2.8 cycles of 1 kHz on screen
            got = scope.get_horizontal_scale().latest
            assert math.isclose(got, 2e-4, rel_tol=REL_TOL), f"set 200us/div, read {got}"

        _run("horizontal scale set/get roundtrip", timebase, failures)

        # --- Sample rate query ---
        def sample_rate() -> None:
            sr = scope.get_sample_rate().latest
            assert math.isfinite(sr) and sr > 0, f"bad sample rate: {sr}"

        _run("sample rate query (SARA?)", sample_rate, failures)

        # --- Acquisition mode + average count ---
        def acq_mode() -> None:
            scope.run()  # the scope only applies ACQW/AVGA changes while acquiring
            scope.set_acquisition_mode(AcquisitionMode.NORMAL)
            assert _cmd_value(scope.get_acquisition_mode()) == AcquisitionMode.NORMAL.value
            scope.set_acquisition_mode(AcquisitionMode.AVERAGE)
            assert _cmd_value(scope.get_acquisition_mode()) == AcquisitionMode.AVERAGE.value
            scope.set_average_count(16)
            assert int(scope.get_average_count().latest) == 16
            scope.set_acquisition_mode(AcquisitionMode.NORMAL)  # restore

        _run("acquisition mode + average count", acq_mode, failures)

        def acq_envelope_unsupported() -> None:
            try:
                scope.set_acquisition_mode(AcquisitionMode.ENVELOPE)
            except NotImplementedError:
                return
            raise AssertionError("ENVELOPE mode should raise NotImplementedError on SDS1000X-E")

        _run("acquisition mode ENVELOPE rejected", acq_envelope_unsupported, failures)

        # --- Trigger configuration ---
        def trigger_config() -> None:
            scope.set_trigger_source(channel=ch)
            scope.set_trigger_type(TriggerType.EDGE)
            scope.set_trigger_slope(TriggerSlope.RISING)
            scope.set_trigger_level(1.0)  # comp square wave crosses 1 V
            scope.set_trigger_mode(TriggerMode.AUTO)

        _run("trigger source/type/slope/level/mode", trigger_config, failures)

        # --- run / state / stop ---
        def run_stop() -> None:
            scope.run()
            time.sleep(0.3)
            assert _cmd_value(scope.get_acquisition_state()) == "RUNNING"
            scope.stop_acquisition()
            time.sleep(0.2)
            assert _cmd_value(scope.get_acquisition_state()) == "STOPPED"

        _run("run -> RUNNING, stop -> STOPPED", run_stop, failures)

        # --- single + digitize via fetch_waveform ---
        def digitize_fetch() -> None:
            # Single-shot waits for a real trigger, so the level must sit inside the signal.
            # Learn the signal midpoint first (works regardless of probe attenuation).
            scope.run()
            time.sleep(0.5)
            vmax = scope.measure(ScopeMeasurementType.VMAX, channel=ch).latest
            vmin = scope.measure(ScopeMeasurementType.VMIN, channel=ch).latest
            scope.set_trigger_level((vmax + vmin) / 2.0)
            scope.set_trigger_slope(TriggerSlope.RISING)
            scope.single()  # arms; fetch_waveform then drives driver.digitize()
            wf: Measurement = scope.fetch_waveform(channel=ch, timeout=3.0)
            volts = wf.values
            assert len(volts) > 100, f"short waveform: {len(volts)} pts"
            assert all(math.isfinite(v) for v in volts), "non-finite sample in waveform"
            assert len(wf.timestamps) == len(volts), "timestamp/voltage length mismatch"
            vpp = max(volts) - min(volts)
            assert vpp > MIN_VPP_V, f"waveform Vpp {vpp:.3f} below {MIN_VPP_V} V — is CH1 driven?"

        _run("single + digitize + fetch_waveform", digitize_fetch, failures)

        # --- Built-in measurements ---
        def measurements() -> None:
            scope.run()  # free-run so PAVA? has a live acquisition
            time.sleep(0.6)
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
            if EXPECTED_DUTY_PERCENT is not None:
                duty = results[ScopeMeasurementType.DUTY_CYCLE]
                assert abs(duty - EXPECTED_DUTY_PERCENT) < DUTY_ABS_TOL, (
                    f"duty {duty:.1f}% vs expected {EXPECTED_DUTY_PERCENT}%"
                )

        _run("measure (VPP/VMAX/VMIN/VAVG/VRMS/FREQ/PERIOD/DUTY)", measurements, failures)

        # --- force_trigger ---
        def force() -> None:
            scope.set_trigger_mode(TriggerMode.NORMAL)
            scope.single()
            scope.force_trigger()
            scope.set_trigger_mode(TriggerMode.AUTO)  # restore

        _run("force_trigger", force, failures)

        # --- trigger status ---
        def trigger_status() -> None:
            scope.run()
            time.sleep(0.2)
            status = _cmd_value(scope.get_trigger_status())
            assert isinstance(status, str) and status, f"bad trigger status: {status!r}"

        _run("get_trigger_status", trigger_status, failures)

        # --- Screenshot (host transfer) ---
        def screenshot() -> None:
            path = os.path.join(tempfile.gettempdir(), "sds1104xe_hw_screenshot.bin")
            scope.save_screenshot(path)
            assert os.path.exists(path) and os.path.getsize(path) > 0, "screenshot file empty/missing"
            os.remove(path)

        _run("save_screenshot (SCDP host transfer)", screenshot, failures)

        def screenshot_to_instrument_unsupported() -> None:
            try:
                scope.save_screenshot("ignored.png", to_instrument=True)
            except NotImplementedError:
                return
            raise AssertionError("save_screenshot(to_instrument=True) should raise NotImplementedError")

        _run("save_screenshot to-instrument rejected", screenshot_to_instrument_unsupported, failures)

        # --- Settings host save (PNSU? read) ---
        def settings_save() -> None:
            # InstroScope.save_settings returns a Command, not the bytes; verify via the written file.
            path = os.path.join(tempfile.gettempdir(), "sds1104xe_hw_setup.bin")
            scope.save_settings(path)
            assert os.path.exists(path) and os.path.getsize(path) > 0, "settings file empty/missing"
            os.remove(path)

        _run("save_settings (PNSU? host read)", settings_save, failures)

        # load_settings host-path issues a PNSU panel-setup write-back, which wedges this
        # SDS1104X-E's USBTMC command interface (recovers only with a power-cycle). Do not
        # exercise it over USB; use load_settings(from_instrument=True) with a USB stick instead.
        _skip("load_settings (PNSU host write-back)", "PNSU write-back wedges the USBTMC interface on this unit")
        _skip("save_settings to-instrument", "requires a USB stick mounted on the scope (UDSK)")

    finally:
        try:
            scope.stop_acquisition()
        except Exception:  # noqa: BLE001 - best-effort safe state
            pass
        scope.close()
    return failures


@pytest.mark.hardware
def test_sds1104xe_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)} check(s))'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
