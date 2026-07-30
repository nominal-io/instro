"""Quick hardware check for the RigolDS1104Z driver, run through InstroScope.

Goes through basically everything the driver implements - vertical/horizontal
settings, acquisition modes, trigger (both EDGE and PULSE, since those hit
different SCPI subsystems on this scope), waveform fetch, measurements,
screenshot/settings I/O.

A handful of things in the driver were guesses because the DS1000Z
programming guide doesn't spell them out: the "no error" code for
check_errors, the NaN sentinel for measure(), how digitize() knows an
acquisition finished (polls :TRIGger:STATus? for "STOP"), what the trigger
status strings actually mean, and mapping DUTY_CYCLE to PDUTy since there's
no plain duty-cycle item. None of that is manual-confirmed, so keep an eye on
those specific checks even when they pass.

Nothing is wired up yet - edit RESOURCE and SIGNAL_CHANNEL below once you've
got the scope hooked up to something. The Probe Comp output on the front
panel works fine as a signal if you don't have anything else handy, just
don't assume it's 1kHz/50% duty like some other vendors' comp signals - check
what your unit actually outputs before filling in EXPECTED_FREQUENCY_HZ.

Run: uv run python tests/contrib/scope/test_rigol_ds1104z_hardware.py
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time
from collections.abc import Callable

import pytest

from instro.contrib.scope.drivers import RigolDS1104Z
from instro.lib.types import Command, Measurement
from instro.scope import (
    AcquisitionMode,
    Coupling,
    InstroScope,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerType,
)

# edit these before running
RESOURCE = "<visa_resource>"
NUM_CHANNELS = 4
SIGNAL_CHANNEL = 1

# leave these None until you know what's actually driving SIGNAL_CHANNEL
EXPECTED_FREQUENCY_HZ: float | None = None
EXPECTED_DUTY_PERCENT: float | None = None

REL_TOL = 0.05
FREQ_REL_TOL = 0.10
DUTY_ABS_TOL = 10.0
MIN_VPP_V = 0.05


def _cmd_value(cmd: Command) -> float | str:
    return next(iter(cmd.channel_data.values()))


def _make_scope() -> InstroScope:
    scope = InstroScope(name="hw_validate", driver=RigolDS1104Z(RESOURCE), num_channels=NUM_CHANNELS, publishers=None)
    scope.open()
    return scope


def _run(name: str, fn: Callable[[], None], failures: list) -> None:
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def run_all() -> list:
    scope = _make_scope()
    failures: list = []
    ch = SIGNAL_CHANNEL
    try:
        # this one exercises check_errors on the way out, so if the "no error" code guess
        # is wrong, basically everything below will fail here first with a RuntimeError
        def sync() -> None:
            cfg = scope.sync_configuration()
            assert cfg.channels, "sync_configuration returned no channel state"
            vs = cfg.channels[ch].vertical_scale
            assert vs is not None and math.isfinite(vs) and vs > 0, f"bad vertical scale: {vs}"

        _run("sync_configuration (getters + check_errors)", sync, failures)

        def vscale() -> None:
            scope.set_vertical_scale(1.0, channel=ch)
            got = scope.get_vertical_scale(channel=ch).latest
            assert math.isclose(got, 1.0, rel_tol=REL_TOL), f"set 1.0 V/div, read {got}"

        _run("vertical scale roundtrip", vscale, failures)

        def voffset() -> None:
            scope.set_vertical_offset(0.0, channel=ch)
            got = scope.get_vertical_offset(channel=ch).latest
            assert abs(got) < 0.05, f"set 0 V offset, read {got}"

        _run("vertical offset roundtrip", voffset, failures)

        def coupling() -> None:
            scope.set_coupling(Coupling.DC, channel=ch)
            assert _cmd_value(scope.get_coupling(channel=ch)) == Coupling.DC.value
            scope.set_coupling(Coupling.AC, channel=ch)
            assert _cmd_value(scope.get_coupling(channel=ch)) == Coupling.AC.value
            scope.set_coupling(Coupling.DC, channel=ch)  # back to default

        _run("coupling roundtrip (AC/DC)", coupling, failures)

        def probe() -> None:
            scope.set_probe_attenuation(10, channel=ch)
            assert math.isclose(scope.get_probe_attenuation(channel=ch).latest, 10, rel_tol=REL_TOL)
            scope.set_probe_attenuation(1, channel=ch)  # 1x, assuming a direct BNC feed
            assert math.isclose(scope.get_probe_attenuation(channel=ch).latest, 1, rel_tol=REL_TOL)

        _run("probe attenuation roundtrip", probe, failures)

        def timebase() -> None:
            scope.set_horizontal_scale(2e-4)
            got = scope.get_horizontal_scale().latest
            assert math.isclose(got, 2e-4, rel_tol=REL_TOL), f"set 200us/div, read {got}"

        _run("horizontal scale roundtrip", timebase, failures)

        def sample_rate() -> None:
            sr = scope.get_sample_rate().latest
            assert math.isfinite(sr) and sr > 0, f"bad sample rate: {sr}"

        _run("sample rate query", sample_rate, failures)

        def acq_mode() -> None:
            scope.set_acquisition_mode(AcquisitionMode.NORMAL)
            assert _cmd_value(scope.get_acquisition_mode()) == AcquisitionMode.NORMAL.value
            scope.set_acquisition_mode(AcquisitionMode.AVERAGE)
            assert _cmd_value(scope.get_acquisition_mode()) == AcquisitionMode.AVERAGE.value
            scope.set_average_count(16)
            assert int(scope.get_average_count().latest) == 16
            scope.set_acquisition_mode(AcquisitionMode.NORMAL)

        _run("acquisition mode + average count", acq_mode, failures)

        def acq_envelope_unsupported() -> None:
            try:
                scope.set_acquisition_mode(AcquisitionMode.ENVELOPE)
            except NotImplementedError:
                return
            raise AssertionError("ENVELOPE should raise NotImplementedError, DS1104Z doesn't have this mode")

        _run("ENVELOPE mode rejected", acq_envelope_unsupported, failures)

        def trigger_edge() -> None:
            scope.set_trigger_type(TriggerType.EDGE)
            scope.set_trigger_source(channel=ch)
            scope.set_trigger_slope(TriggerSlope.RISING)
            scope.set_trigger_level(0.0)
            scope.set_trigger_mode(TriggerMode.AUTO)

        _run("EDGE trigger config", trigger_edge, failures)

        # source/level get routed to a different SCPI subsystem depending on which trigger
        # type is active, so this is really checking that the dispatch logic actually works
        # against real hardware and not just that the driver picked a plausible-looking string
        def trigger_pulse() -> None:
            scope.set_trigger_type(TriggerType.PULSE)
            scope.set_trigger_source(channel=ch)
            scope.set_trigger_level(0.0)
            scope.set_trigger_type(TriggerType.EDGE)
            scope.set_trigger_source(channel=ch)

        _run("PULSE trigger config (different subsystem)", trigger_pulse, failures)

        def run_stop() -> None:
            scope.run()
            time.sleep(0.3)
            assert _cmd_value(scope.get_acquisition_state()) == "RUNNING"
            scope.stop_acquisition()
            time.sleep(0.2)
            assert _cmd_value(scope.get_acquisition_state()) == "STOPPED"

        _run("run/stop -> acquisition state", run_stop, failures)

        # if this one hangs and times out, the STOP-means-done assumption in digitize() is
        # probably the culprit - worth printing the raw :TRIGger:STATus? reply to check
        def digitize_fetch() -> None:
            scope.run()
            time.sleep(0.5)
            vmax = scope.measure(ScopeMeasurementType.VMAX, channel=ch).latest
            vmin = scope.measure(ScopeMeasurementType.VMIN, channel=ch).latest
            scope.set_trigger_level((vmax + vmin) / 2.0)
            scope.set_trigger_slope(TriggerSlope.RISING)
            scope.single()
            wf: Measurement = scope.fetch_waveform(channel=ch, timeout=5.0)
            volts = wf.values
            assert len(volts) > 100, f"short waveform: {len(volts)} pts"
            assert all(math.isfinite(v) for v in volts), "non-finite sample in waveform"
            assert len(wf.timestamps) == len(volts), "timestamp/voltage length mismatch"
            vpp = max(volts) - min(volts)
            assert vpp > MIN_VPP_V, f"waveform Vpp {vpp:.3f} below {MIN_VPP_V} V - is {ch} actually driven?"

        _run("single + digitize + fetch_waveform", digitize_fetch, failures)

        def measurements() -> None:
            scope.run()
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
                # this is the PDUTy mapping - compare against the front panel if it looks off
                duty = results[ScopeMeasurementType.DUTY_CYCLE]
                assert abs(duty - EXPECTED_DUTY_PERCENT) < DUTY_ABS_TOL, (
                    f"duty {duty:.1f}% vs expected {EXPECTED_DUTY_PERCENT}%"
                )

        _run("built-in measurements (all 8 types)", measurements, failures)

        def force() -> None:
            scope.set_trigger_mode(TriggerMode.NORMAL)
            scope.single()
            scope.force_trigger()
            scope.set_trigger_mode(TriggerMode.AUTO)

        _run("force_trigger", force, failures)

        def trigger_status() -> None:
            scope.run()
            time.sleep(0.2)
            status = _cmd_value(scope.get_trigger_status())
            print(f"    (mapped status while running: {status!r} - sanity check this looks right)")
            assert isinstance(status, str) and status, f"bad trigger status: {status!r}"

        _run("get_trigger_status", trigger_status, failures)

        def screenshot() -> None:
            path = os.path.join(tempfile.gettempdir(), "ds1104z_hw_screenshot.bmp")
            scope.save_screenshot(path)
            assert os.path.exists(path) and os.path.getsize(path) > 0, "screenshot file empty/missing"
            os.remove(path)

        _run("save_screenshot", screenshot, failures)

        def screenshot_to_instrument_unsupported() -> None:
            try:
                scope.save_screenshot("ignored.bmp", to_instrument=True)
            except NotImplementedError:
                return
            raise AssertionError("to_instrument=True should raise NotImplementedError")

        _run("save_screenshot(to_instrument=True) rejected", screenshot_to_instrument_unsupported, failures)

        def settings_roundtrip() -> None:
            path = os.path.join(tempfile.gettempdir(), "ds1104z_hw_setup.bin")
            scope.save_settings(path)
            assert os.path.exists(path) and os.path.getsize(path) > 0, "settings file empty/missing"
            scope.load_settings(path)  # manual says this has to be exactly what save_settings wrote
            os.remove(path)

        _run("save_settings + load_settings round trip", settings_roundtrip, failures)

        def settings_to_instrument_unsupported() -> None:
            try:
                scope.save_settings("ignored.bin", to_instrument=True)
            except NotImplementedError:
                return
            raise AssertionError("to_instrument=True should raise NotImplementedError")

        _run("save_settings(to_instrument=True) rejected", settings_to_instrument_unsupported, failures)

        def settings_from_instrument_unsupported() -> None:
            try:
                scope.load_settings("ignored.bin", from_instrument=True)
            except NotImplementedError:
                return
            raise AssertionError("from_instrument=True should raise NotImplementedError")

        _run("load_settings(from_instrument=True) rejected", settings_from_instrument_unsupported, failures)

    finally:
        try:
            scope.stop_acquisition()
        except Exception:  # noqa: BLE001 - best-effort safe state
            pass
        scope.close()
    return failures


@pytest.mark.hardware
def test_ds1104z_hardware() -> None:
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)} check(s))'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
