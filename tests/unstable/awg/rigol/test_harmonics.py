"""Rigol DG1022Z hardware test: 60s of harmonics output that changes configuration every 15s."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z
from instro.unstable.awg.types import HarmonicType, Sine

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_RESOURCE to the bench unit's VISA resource string. Set VISA_BACKEND to
# "@ivi" or "" for the system VISA library, or "@py" for pyvisa-py.
VISA_RESOURCE = "USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR"
VISA_BACKEND = "@py"

CHANNEL = 1
FREQUENCY_HZ = 1000.0
INTERVAL_S = 15.0


@dataclass(frozen=True)
class _HarmonicConfig:
    order: int
    harm_type: HarmonicType
    user_harmonics: str | None = None


CONFIGS: list[_HarmonicConfig] = [
    _HarmonicConfig(order=2, harm_type=HarmonicType.ALL),
    _HarmonicConfig(order=4, harm_type=HarmonicType.EVEN),
    _HarmonicConfig(order=5, harm_type=HarmonicType.ODD),
    _HarmonicConfig(order=8, harm_type=HarmonicType.USER, user_harmonics="1010100"),
]


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


def test_60s_harmonics_changes_configuration_every_15s(driver: RigolDG1022Z) -> None:
    """Enable harmonics on a Sine carrier on channel 1, changing order/type every 15s for 60s total."""

    def _apply(config: _HarmonicConfig) -> None:
        driver.enable_harmonics(CHANNEL, config.order, config.harm_type, user_harmonics=config.user_harmonics)
        driver.check_errors()
        assert driver._visa.query(f":SOUR{CHANNEL}:HARM:STAT?").strip() == "ON"
        assert float(driver._visa.query(f":SOUR{CHANNEL}:HARM:ORDE?")) == config.order
        assert driver._visa.query(f":SOUR{CHANNEL}:HARM:TYP?").strip() == config.harm_type.value

    driver.set_waveform(CHANNEL, Sine(frequency_hz=FREQUENCY_HZ))
    _apply(CONFIGS[0])

    try:
        driver.output_enable(CHANNEL, True)
        driver.check_errors()
        assert driver.get_output_state(CHANNEL) is True

        for config in CONFIGS[1:]:
            time.sleep(INTERVAL_S)
            _apply(config)
            assert isinstance(driver.get_waveform(CHANNEL), Sine)

        time.sleep(INTERVAL_S)
    finally:
        driver.output_enable(CHANNEL, False)

    assert driver.get_output_state(CHANNEL) is False
