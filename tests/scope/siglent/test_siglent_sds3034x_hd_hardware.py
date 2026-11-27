"""Verify an SDS3034X HD with a 10x probe from CH3 to the front-panel compensation output.

Set INSTRO_SDS3034X_RESOURCE to the VXI-11 VISA resource and run this file directly.
Set INSTRO_SDS3034X_IDLE_CHANNEL to an unwired channel to also check channel isolation.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from xml.etree import ElementTree

import pytest

from instro.contrib.drivers import SiglentSDS3034XHD
from instro.lib.transports.visa import VisaDriver
from instro.scope import (
    AcquisitionMode,
    Coupling,
    InstroScope,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerType,
)

pytestmark = pytest.mark.hardware
CHANNEL = 3


@pytest.fixture
def bench(tmp_path):
    resource = os.environ.get("INSTRO_SDS3034X_RESOURCE")
    if not resource:
        pytest.skip("Set INSTRO_SDS3034X_RESOURCE to the scope's VISA resource")
    wire = VisaDriver(resource)
    driver = SiglentSDS3034XHD(resource)
    scope = InstroScope(name="scope", driver=driver, num_channels=4, publishers=None)
    wire.open()
    try:
        assert wire.query("*IDN?").split(",")[1] == "SDS3034X HD"
        scope.open()
        try:
            status = wire.query(":TRIG:STAT?").strip()
            mode = wire.query(":TRIG:MODE?").strip()
            transfer = {
                key: wire.query(f":WAV:{key}?").strip() for key in ("SOUR", "WIDT", "BYT", "STAR", "POIN", "INT")
            }
            setup = str(tmp_path / "original.xml")
            scope.save_settings(setup)
            assert ElementTree.parse(setup).getroot().tag == "nsp_config"
            try:
                yield scope, wire
            finally:
                scope.load_settings(setup)
                for key, value in transfer.items():
                    wire.write(f":WAV:{key} {value}")
                if status.upper() == "STOP":
                    scope.stop_acquisition()
                elif mode.upper().startswith("SING"):
                    scope.single()
                else:
                    scope.run()
                driver.check_errors()
        finally:
            scope.close()
    finally:
        wire.close()


def test_controls(bench, tmp_path):
    scope, wire = bench
    scope.run()
    assert len(scope.sync_configuration().channels) == 4
    scope.set_probe_attenuation(10, channel=CHANNEL)
    scope.set_vertical_scale(1, channel=CHANNEL)
    scope.set_vertical_offset(-1, channel=CHANNEL)
    scope.set_coupling(Coupling.AC, channel=CHANNEL)
    scope.set_horizontal_scale(500e-6)
    state = scope.sync_configuration()
    ch = state.channels[CHANNEL]
    assert (ch.probe_attenuation, ch.vertical_scale, ch.vertical_offset, ch.coupling) == (10, 1, -1, Coupling.AC)
    assert state.horizontal_scale == pytest.approx(500e-6)
    scope.set_acquisition_mode(AcquisitionMode.NORMAL)
    scope.set_average_count(16)
    assert scope.get_acquisition_mode().latest == "NORMAL"
    for mode in (
        AcquisitionMode.AVERAGE,
        AcquisitionMode.HIGH_RESOLUTION,
        AcquisitionMode.PEAK_DETECT,
        AcquisitionMode.NORMAL,
    ):
        scope.set_acquisition_mode(mode)
        assert scope.get_acquisition_mode().latest == mode.value
        if mode is AcquisitionMode.AVERAGE:
            scope.set_average_count(32)
            assert scope.get_average_count().latest == 32
    scope.set_trigger_type(TriggerType.EDGE)
    scope.set_trigger_source(CHANNEL)
    scope.set_trigger_type(TriggerType.PULSE)
    scope.set_trigger_level(1)
    assert wire.query(":TRIG:PULS:SOUR?").strip() == "C3"
    assert float(wire.query(":TRIG:PULS:LEV?")) == pytest.approx(1)
    scope.set_trigger_type(TriggerType.EDGE)
    scope.set_trigger_slope(TriggerSlope.EITHER)
    assert wire.query(":TRIG:EDGE:SLOP?").strip().upper().startswith("ALT")
    scope.set_trigger_mode(TriggerMode.NORMAL)
    scope.set_trigger_level(4.0)
    scope.single()
    scope.force_trigger()
    assert scope.get_acquisition_state().latest == "STOPPED"
    scope.run()
    assert wire.query(":TRIG:MODE?").strip().upper() == "NORMAL"
    scope.stop_acquisition()
    assert scope.get_acquisition_state().latest == "STOPPED"
    assert scope.get_trigger_status().latest == "READY"
    scope.run()
    assert scope.get_acquisition_state().latest == "RUNNING"
    png = tmp_path / "screen.png"
    scope.save_screenshot(str(png))
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    settings = str(tmp_path / "roundtrip.xml")
    scope.save_settings(settings)
    scope.set_vertical_scale(2, channel=CHANNEL)
    scope.load_settings(settings)
    assert scope.get_vertical_scale(channel=CHANNEL).latest == 1


def test_compensation_waveform(bench):
    scope, wire = bench
    wire.write(f":CHAN{CHANNEL}:SWIT ON")
    idle = os.environ.get("INSTRO_SDS3034X_IDLE_CHANNEL")
    if idle:
        assert int(idle) in {1, 2, 4}
        wire.write(f":CHAN{int(idle)}:SWIT ON")
        scope.set_vertical_scale(1, channel=int(idle))
        scope.set_vertical_offset(0, channel=int(idle))
    wire.write(":TIM:DEL 0")
    scope.set_acquisition_mode(AcquisitionMode.NORMAL)
    scope.set_coupling(Coupling.DC, channel=CHANNEL)
    scope.set_probe_attenuation(10, channel=CHANNEL)
    scope.set_vertical_scale(0.5, channel=CHANNEL)
    scope.set_vertical_offset(-1.5, channel=CHANNEL)
    scope.set_horizontal_scale(500e-6)
    scope.set_trigger_type(TriggerType.EDGE)
    scope.set_trigger_source(CHANNEL)
    scope.set_trigger_level(1.5)
    scope.set_trigger_slope(TriggerSlope.RISING)
    scope.set_trigger_mode(TriggerMode.AUTO)
    scope.single()
    trace = scope.fetch_waveform(channel=CHANNEL, timeout=5)
    samples = trace.values
    times = trace.timestamps
    n = len(samples)
    assert n == len(times) == int(float(wire.query(":ACQ:POIN?")))
    sample_rate = float(scope.get_sample_rate().latest)
    assert (times[-1] - times[0]) == pytest.approx((n - 1) * 1e9 / sample_rate, abs=2)
    readbacks = {m: float(scope.measure(m, channel=CHANNEL).latest) for m in ScopeMeasurementType}
    assert all(math.isfinite(x) for x in readbacks.values())
    low, high = min(samples), max(samples)
    amplitude = high - low
    assert low == pytest.approx(0, abs=0.15) and high == pytest.approx(3, abs=0.3)
    assert amplitude == pytest.approx(readbacks[ScopeMeasurementType.VPP], abs=0.10)
    crossings = [times[i] for i in range(1, n) if samples[i - 1] < 1.5 <= samples[i]]
    assert len(crossings) >= 3
    freq = (len(crossings) - 1) * 1e9 / (crossings[-1] - crossings[0])
    duty = 100 * sum(v >= 1.5 for v in samples) / n
    assert freq == pytest.approx(1000, rel=0.02)
    assert readbacks[ScopeMeasurementType.FREQUENCY] == pytest.approx(freq, rel=0.01)
    assert readbacks[ScopeMeasurementType.PERIOD] == pytest.approx(1 / freq, rel=0.01)
    assert duty == pytest.approx(50, abs=2)
    assert readbacks[ScopeMeasurementType.DUTY_CYCLE] == pytest.approx(duty, abs=2)
    print(f"CH{CHANNEL}: {n:,} points, {freq:.2f} Hz, {duty:.2f}% high, {low:.4f} to {high:.4f} V, {amplitude:.4f} Vpp")
    if idle:
        other = scope.fetch_waveform(channel=int(idle)).values
        assert len(other) == n
        assert max(other) - min(other) < 0.25 * amplitude


if __name__ == "__main__":
    if not os.environ.get("INSTRO_SDS3034X_RESOURCE"):
        raise SystemExit("Set INSTRO_SDS3034X_RESOURCE to the SDS3034X HD VXI-11 resource")
    raise SystemExit(pytest.main(["-m", "hardware", "-q", "-s", str(Path(__file__))]))
