"""Rigol DG1022Z hardware test: a 60s internal burst that switches carrier waveform every 15s."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z
from instro.unstable.awg.types import BurstType, Pulse, Sawtooth, Sine, Square, Waveform

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_RESOURCE to the bench unit's VISA resource string. Set VISA_BACKEND to
# "@ivi" or "" for the system VISA library, or "@py" for pyvisa-py.
VISA_RESOURCE = "USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR"
VISA_BACKEND = "@py"

CHANNEL = 1
FREQUENCY_HZ = 1000.0
INTERVAL_S = 15.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shutdown_outputs(driver: RigolDG1022Z) -> None:
    driver.output_enable(CHANNEL, False)


def _reset_driver(driver: RigolDG1022Z) -> None:
    driver._visa.write("*CLS")
    driver._visa.write("*RST")
    time.sleep(0.5)
    driver._visa.write("*CLS")
    driver.check_errors()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def driver() -> Iterator[RigolDG1022Z]:
    awg_driver = RigolDG1022Z(
        VisaConfig(
            visa_resource=VISA_RESOURCE,
            visa_backend=VISA_BACKEND,
        )
    )
    opened = False
    try:
        awg_driver.open()
        opened = True
        yield awg_driver
    finally:
        if opened:
            _shutdown_outputs(awg_driver)
        awg_driver.close()


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: RigolDG1022Z) -> None:
    _reset_driver(driver)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_60s_internal_burst_switches_waveform_every_15s(driver: RigolDG1022Z) -> None:
    """Burst channel 1 with an internally triggered square wave, switching carrier every 15s for 60s total."""
    waveforms: list[Waveform] = [
        Square(frequency_hz=FREQUENCY_HZ),
        Sawtooth(frequency_hz=FREQUENCY_HZ),
        Sine(frequency_hz=FREQUENCY_HZ),
        Pulse(frequency_hz=FREQUENCY_HZ, width_s=0.0002),
    ]

    driver.set_waveform(CHANNEL, waveforms[0])
    driver.burst(CHANNEL, BurstType.NCYCLE)
    driver.check_errors()
    assert driver._visa.query(f":SOUR{CHANNEL}:BURS:TRIG:SOUR?").strip() == "INT"

    try:
        driver.output_enable(CHANNEL, True)
        driver.check_errors()
        assert driver.get_output_state(CHANNEL) is True

        for waveform in waveforms[1:]:
            time.sleep(INTERVAL_S)
            driver.set_waveform(CHANNEL, waveform)
            driver.check_errors()
            readback = driver.get_waveform(CHANNEL)
            assert type(readback) is type(waveform), f"programmed {waveform}, read back {readback}"
            assert driver._visa.query(":SOUR1:BURS:STAT?").strip() == "ON"

        time.sleep(INTERVAL_S)
    finally:
        driver.output_enable(CHANNEL, False)

    assert driver.get_output_state(CHANNEL) is False
