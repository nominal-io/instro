"""Tests for the transport-free VirtualDMM driver."""

from __future__ import annotations

import pytest

from instro.dmm import InstroDMM, MeasurementFunction
from instro.dmm.drivers import VirtualDMM


def test_reads_stimulus_with_defaults_for_the_rest() -> None:
    dmm = InstroDMM(name="dmm", driver=VirtualDMM({MeasurementFunction.DC_VOLTAGE: 3.3}))
    with dmm:
        assert dmm.read_dc_voltage().latest == pytest.approx(3.3, rel=0.01)
        assert dmm.read_four_wire_resistance().latest == pytest.approx(1000.0, rel=0.01)


def test_accepts_every_measurement_setting() -> None:
    dmm = InstroDMM(name="dmm", driver=VirtualDMM())
    with dmm:
        for function in MeasurementFunction:
            dmm.set_measurement_function(function)
            dmm.set_range(10.0)
            dmm.set_range(None)
            dmm.set_aperture_nplc(10)
            dmm.set_aperture_seconds(0.1)
            dmm.set_digits(6)
            assert dmm.read().latest > 0
