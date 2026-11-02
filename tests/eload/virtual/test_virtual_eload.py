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


def test_accepts_every_setting_and_short_pulls_terminals_to_zero() -> None:
    eload = InstroELoad(name="eload", driver=VirtualELoad(source_voltage=5.0, source_resistance=1.0))
    with eload:
        eload.set_mode(LoadMode.CV)
        eload.set_range(10.0)
        eload.set_level(1.0, curr_limit=0.5)
        for direction in SlewRateDirection:
            eload.set_slewrate(direction, 0.1)
        eload.output_enable(True)
        assert eload.get_current().latest == pytest.approx(0.5, rel=0.01)

        eload.short_output(True)
        assert eload.get_voltage().latest == 0.0
        assert eload.get_current().latest == pytest.approx(5.0, rel=0.01)


def test_rejects_channels_other_than_one() -> None:
    eload = InstroELoad(name="eload", driver=VirtualELoad())
    with eload, pytest.raises(ValueError, match="single channel"):
        eload.get_voltage(channel=2)
