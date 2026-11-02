"""Tests for the transport-free VirtualELoad driver."""

from __future__ import annotations

import pytest

from instro.eload import InstroELoad, LoadMode, SlewRateDirection
from instro.eload.drivers import VirtualELoad


@pytest.mark.parametrize(
    ("mode", "level"),
    [(LoadMode.CC, 2.0), (LoadMode.CR, 5.5), (LoadMode.CV, 11.0), (LoadMode.CP, 22.0)],
)
def test_mode_level_sets_the_operating_point_on_the_source(mode: LoadMode, level: float) -> None:
    # every case draws 2 A from the 12 V / 0.5 ohm default source, sagging it to 11 V
    eload = InstroELoad(name="eload", driver=VirtualELoad())
    with eload:
        eload.set_mode(mode)
        eload.set_level(level)
        assert eload.get_current().latest == 0.0
        assert eload.get_voltage().latest == pytest.approx(12.0, rel=0.01)

        eload.output_enable(True)
        assert eload.get_current().latest == pytest.approx(2.0, rel=0.01)
        assert eload.get_voltage().latest == pytest.approx(11.0, rel=0.01)


@pytest.mark.parametrize(
    ("mode", "level", "volts", "amps"),
    [
        (LoadMode.CC, 100.0, 0.0, 24.0),  # past the 24 A short-circuit current
        (LoadMode.CV, 13.0, 12.0, 0.0),  # above the source voltage
        (LoadMode.CP, 500.0, 6.0, 12.0),  # past the 72 W maximum power
    ],
)
def test_unreachable_level_stops_at_the_source_limit(mode: LoadMode, level: float, volts: float, amps: float) -> None:
    eload = InstroELoad(name="eload", driver=VirtualELoad())
    with eload:
        eload.set_mode(mode)
        eload.set_level(level)
        eload.output_enable(True)
        assert eload.get_voltage().latest == pytest.approx(volts, rel=0.01, abs=1e-9)
        assert eload.get_current().latest == pytest.approx(amps, rel=0.01, abs=1e-9)


def test_each_mode_keeps_its_level_and_cv_draws_nothing_until_set() -> None:
    driver = VirtualELoad()
    eload = InstroELoad(name="eload", driver=driver)
    with eload:
        eload.output_enable(True)
        eload.set_mode(LoadMode.CV)
        driver.source_voltage = 24.0
        assert eload.get_current().latest == 0.0

        driver.source_voltage = 12.0
        eload.set_mode(LoadMode.CC)
        eload.set_level(2.0)
        eload.set_mode(LoadMode.CR)
        eload.set_level(5.5)
        eload.set_mode(LoadMode.CC)
        assert eload.get_current().latest == pytest.approx(2.0, rel=0.01)


def test_accepts_every_setting() -> None:
    eload = InstroELoad(name="eload", driver=VirtualELoad(source_voltage=5.0, source_resistance=1.0))
    with eload:
        eload.set_mode(LoadMode.CV)
        eload.set_range(10.0)
        eload.set_level(1.0, curr_limit=0.5)
        for direction in SlewRateDirection:
            eload.set_slewrate(direction, 0.1)
        eload.output_enable(True)
        assert eload.get_current().latest == pytest.approx(0.5, rel=0.01)


def test_short_switches_the_input_and_persists_like_bk85xxb() -> None:
    eload = InstroELoad(name="eload", driver=VirtualELoad(source_voltage=5.0, source_resistance=1.0))
    with eload:
        eload.short_output(True)
        assert eload.get_voltage().latest == 0.0
        assert eload.get_current().latest == pytest.approx(5.0, rel=0.01)

        eload.output_enable(False)
        eload.output_enable(True)
        assert eload.get_voltage().latest == 0.0

        eload.short_output(False)
        assert eload.get_current().latest == 0.0
        assert eload.get_voltage().latest == pytest.approx(5.0, rel=0.01)


def test_rejects_a_negative_cv_current_limit_without_changing_the_level() -> None:
    eload = InstroELoad(name="eload", driver=VirtualELoad())
    with eload:
        eload.set_mode(LoadMode.CV)
        eload.set_level(11.0)
        with pytest.raises(ValueError, match="curr_limit must be non-negative"):
            eload.set_level(5.0, curr_limit=-1.0)
        eload.output_enable(True)
        assert eload.get_current().latest == pytest.approx(2.0, rel=0.01)


def test_rejects_an_invalid_source_set_at_runtime() -> None:
    driver = VirtualELoad()
    with pytest.raises(ValueError, match="source_resistance must be positive"):
        driver.source_resistance = 0.0
    with pytest.raises(ValueError, match="source_voltage must not be negative"):
        driver.source_voltage = -5.0


def test_rejects_channels_other_than_one() -> None:
    eload = InstroELoad(name="eload", driver=VirtualELoad())
    with eload, pytest.raises(ValueError, match="single channel"):
        eload.get_voltage(channel=2)
