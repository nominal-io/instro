"""Unit tests for InstroScope channel naming, including the legacy_naming flag."""

from unittest.mock import MagicMock

from instro.unstable.scope import InstroScope
from instro.unstable.scope.driver import ScopeDriverBase
from instro.unstable.scope.types import (
    AcquisitionMode,
    AcquisitionState,
    Coupling,
    ScopeMeasurementType,
    WaveformData,
)


def _stub_driver() -> MagicMock:
    """A ScopeDriverBase MagicMock with the return values needed for the channels under test."""
    driver = MagicMock(spec=ScopeDriverBase)
    driver.get_vertical_scale.return_value = 0.5
    driver.get_vertical_offset.return_value = 0.0
    driver.get_coupling.return_value = Coupling.DC
    driver.get_probe_attenuation.return_value = 10.0
    driver.get_horizontal_scale.return_value = 1e-6
    driver.get_sample_rate.return_value = 1e9
    driver.get_acquisition_mode.return_value = AcquisitionMode.NORMAL
    driver.get_average_count.return_value = 4
    driver.get_acquisition_state.return_value = AcquisitionState.STOPPED
    driver.measure.return_value = 1.23
    driver.fetch_waveform.return_value = WaveformData(times=[0, 1, 2], voltages=[0.0, 0.1, 0.2])
    return driver


# --- Default (v1.0) naming ---


def test_default_naming_set_vertical_scale_uses_dot_separator() -> None:
    scope = InstroScope(name="ut", driver=_stub_driver(), num_channels=2)
    command = scope.set_vertical_scale(0.1, channel=1)
    assert "ut.ch1.vscale.cmd" in command.channel_data


def test_default_naming_probe_attenuation_uses_full_word() -> None:
    scope = InstroScope(name="ut", driver=_stub_driver(), num_channels=2)
    command = scope.set_probe_attenuation(10.0, channel=1)
    assert "ut.ch1.probe_attenuation.cmd" in command.channel_data


def test_default_naming_measure_uses_dot_separator() -> None:
    scope = InstroScope(name="ut", driver=_stub_driver(), num_channels=2)
    measurement = scope.measure(ScopeMeasurementType.VRMS, channel=1)
    assert "ut.ch1.vrms" in measurement.channel_data  # type: ignore[union-attr]


# --- Legacy naming ---


def test_legacy_naming_set_vertical_scale_uses_underscore_separator() -> None:
    scope = InstroScope(
        name="ut",
        driver=_stub_driver(),
        num_channels=2,
        legacy_naming=True,
    )
    command = scope.set_vertical_scale(0.1, channel=1)
    assert "ut.ch1_vscale.cmd" in command.channel_data
    assert "ut.ch1.vscale.cmd" not in command.channel_data


def test_legacy_naming_probe_attenuation_uses_abbreviated_form() -> None:
    """Legacy descriptor was `probe_atten`; under legacy_naming the abbreviation is restored."""
    scope = InstroScope(
        name="ut",
        driver=_stub_driver(),
        num_channels=2,
        legacy_naming=True,
    )
    command = scope.set_probe_attenuation(10.0, channel=1)
    assert "ut.ch1_probe_atten.cmd" in command.channel_data
    assert "probe_attenuation" not in next(iter(command.channel_data.keys()))


def test_legacy_naming_measure_uses_underscore_separator() -> None:
    scope = InstroScope(
        name="ut",
        driver=_stub_driver(),
        num_channels=2,
        legacy_naming=True,
    )
    measurement = scope.measure(ScopeMeasurementType.VRMS, channel=1)
    assert "ut.ch1_vrms" in measurement.channel_data  # type: ignore[union-attr]


def test_legacy_naming_fetch_waveform_uses_underscore_separator() -> None:
    scope = InstroScope(
        name="ut",
        driver=_stub_driver(),
        num_channels=2,
        legacy_naming=True,
    )
    measurement = scope.fetch_waveform(channel=1)
    assert "ut.ch1_waveform" in measurement.channel_data
    assert "ut.ch1.waveform" not in measurement.channel_data
