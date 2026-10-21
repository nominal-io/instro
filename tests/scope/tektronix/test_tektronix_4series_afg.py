"""Mocked-transport tests for the Tektronix4SeriesMSO AFG view; the device lives with the scope, so these do too."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.awg import AWGDriverBase
from instro.awg.types import AmplitudeMeasurementUnit, Pulse, Sawtooth, Sine, Square, StaticValue, Triangle
from instro.lib.exceptions import FeatureNotSupportedError
from instro.scope.drivers import Tektronix4SeriesMSO

NO_EVENTS = b'0,"No events to report - queue empty"'


@pytest.fixture
def visa() -> Iterator[MagicMock]:
    with patch("instro.scope.drivers.tektronix_4series.VisaDriver", autospec=True) as driver_cls:
        visa = driver_cls.return_value
        visa.query.return_value = "0"
        visa.query_raw.return_value = NO_EVENTS
        yield visa


@pytest.fixture
def afg(visa: MagicMock) -> AWGDriverBase:
    return Tektronix4SeriesMSO("<visa_resource>").awg


def _writes(visa: MagicMock) -> list[str]:
    return [c.args[0] for c in visa.write.call_args_list]


def test_square_programs_function_frequency_and_duty(afg: AWGDriverBase, visa: MagicMock) -> None:
    afg.set_waveform(1, Square(frequency_hz=1000.0, duty_cycle_pct=25.0))
    assert _writes(visa) == ["AFG:FUNCtion SQUare", "AFG:FREQuency 1000.0", "AFG:SQUare:DUty 25.0"]


def test_sawtooth_and_triangle_share_ramp_and_differ_in_symmetry(afg: AWGDriverBase, visa: MagicMock) -> None:
    afg.set_waveform(1, Sawtooth(frequency_hz=10.0))
    assert _writes(visa)[-1] == "AFG:RAMP:SYMmetry 100"
    afg.set_waveform(1, Triangle(frequency_hz=10.0))
    assert _writes(visa)[-1] == "AFG:RAMP:SYMmetry 50"


def test_static_value_programs_dc_offset(afg: AWGDriverBase, visa: MagicMock) -> None:
    afg.set_waveform(1, StaticValue(value=1.5))
    assert _writes(visa) == ["AFG:FUNCtion DC", "AFG:OFFSet 1.5"]


@pytest.mark.parametrize(
    "waveform, match",
    [
        (Sine(frequency_hz=1000.0, phase_deg=90.0), "phase"),
        (Pulse(frequency_hz=1000.0, width_s=1e-4, delay_s=1e-5), "delay"),
    ],
)
def test_unprogrammable_waveform_parameters_raise_before_any_write(
    afg: AWGDriverBase, visa: MagicMock, waveform: Sine | Pulse, match: str
) -> None:
    with pytest.raises(FeatureNotSupportedError, match=match):
        afg.set_waveform(1, waveform)
    visa.write.assert_not_called()


@pytest.mark.parametrize(
    "func, extra, expected",
    [
        ("SINE", {}, Sine(frequency_hz=1000.0)),
        ("RAMP", {"AFG:RAMP:SYMmetry?": "100.0000"}, Sawtooth(frequency_hz=1000.0)),
        ("RAMP", {"AFG:RAMP:SYMmetry?": "50.0000"}, Triangle(frequency_hz=1000.0)),
        ("PULSE", {"AFG:PULse:WIDth?": "1.0E-4"}, Pulse(frequency_hz=1000.0, width_s=1e-4)),
    ],
)
def test_get_waveform_reads_back_shape(
    afg: AWGDriverBase, visa: MagicMock, func: str, extra: dict[str, str], expected: object
) -> None:
    responses = {"AFG:FUNCtion?": func, "AFG:FREQuency?": "1.0E+3", **extra}
    visa.query.side_effect = lambda cmd: responses.get(cmd, "0")
    assert afg.get_waveform(1) == expected


def test_amplitude_is_vpp_only(afg: AWGDriverBase, visa: MagicMock) -> None:
    afg.set_amplitude(1, 2.0, AmplitudeMeasurementUnit.VPP)
    visa.write.assert_called_once_with("AFG:AMPLitude 2.0")
    with pytest.raises(FeatureNotSupportedError, match="VPP"):
        afg.set_amplitude(1, 1.0, AmplitudeMeasurementUnit.VRMS)


def test_output_enable_drives_state_not_mode(afg: AWGDriverBase, visa: MagicMock) -> None:
    afg.output_enable(1, True)
    afg.output_enable(1, False)
    assert _writes(visa) == ["AFG:OUTPut:STATE ON", "AFG:OUTPut:STATE OFF"]


def test_output_load_maps_high_z_and_fifty_ohms(afg: AWGDriverBase, visa: MagicMock) -> None:
    afg.set_output_load(1, None)
    afg.set_output_load(1, 50)
    assert _writes(visa) == ["AFG:OUTPut:LOAd:IMPEDance HIGHZ", "AFG:OUTPut:LOAd:IMPEDance FIFty"]
    with pytest.raises(FeatureNotSupportedError, match="50 ohm"):
        afg.set_output_load(1, 75)


def test_only_channel_one_exists(afg: AWGDriverBase) -> None:
    with pytest.raises(FeatureNotSupportedError, match="single output"):
        afg.get_offset(2)


def test_square_duty_outside_the_afg_range_is_rejected_before_any_write(afg: AWGDriverBase, visa: MagicMock) -> None:
    with pytest.raises(FeatureNotSupportedError, match="10 to 90"):
        afg.set_waveform(1, Square(frequency_hz=1000.0, duty_cycle_pct=5.0))
    visa.write.assert_not_called()


def test_modulation_and_sweep_are_unsupported_not_unimplemented(afg: AWGDriverBase) -> None:
    with pytest.raises(FeatureNotSupportedError, match="modulation"):
        afg.modulation_enable(1, True)
    with pytest.raises(FeatureNotSupportedError, match="sweep"):
        afg.sweep_enable(1, True)


def test_get_waveform_reports_a_function_the_awg_types_cannot_represent(afg: AWGDriverBase, visa: MagicMock) -> None:
    responses = {"AFG:FUNCtion?": "NOISE", "AFG:FREQuency?": "1.0E+3"}
    visa.query.side_effect = lambda cmd: responses.get(cmd, "0")
    with pytest.raises(FeatureNotSupportedError, match="NOISE"):
        afg.get_waveform(1)
