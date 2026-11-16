"""Software tests for the Saleae Logic MSO scope driver (unstable), against a fake ``saleae.mso_api``."""

import math
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from instro.scope.types import AcquisitionState, Coupling, ScopeMeasurementType, TriggerMode, TriggerSlope
from instro.unstable.scope.drivers import SaleaeLogicMSO


class _CaptureTimeoutError(Exception):
    pass


class _AnalogSettings:
    """Stand-in that snaps like the API: to the device maximum over a power-of-two downsample."""

    def __init__(self, sample_rate: float) -> None:
        self.sample_rate = sample_rate

    def validate_and_update_sample_rate_for(self, part_number: SimpleNamespace) -> int:
        ratio = 1
        while part_number.max_analog_sample_rate_hz / (ratio * 2) >= self.sample_rate:
            ratio *= 2
        self.sample_rate = part_number.max_analog_sample_rate_hz / ratio
        return ratio


@pytest.fixture
def sdk() -> Iterator[MagicMock]:
    sdk = MagicMock()
    sdk.exceptions.CaptureTimeoutError = _CaptureTimeoutError
    sdk.AnalogSettings = _AnalogSettings
    sdk.MSO.return_value.part_number = SimpleNamespace(num_analog_channels=2, max_analog_sample_rate_hz=1.6e9)
    saleae = MagicMock(mso_api=sdk)
    with patch.dict(sys.modules, {"saleae": saleae, "saleae.mso_api": sdk}):
        yield sdk


@pytest.fixture
def mso(sdk: MagicMock) -> MagicMock:
    return sdk.MSO.return_value


@pytest.fixture
def scope(sdk: MagicMock) -> SaleaeLogicMSO:
    driver = SaleaeLogicMSO()
    driver.open()
    return driver


def _export(voltages: list[float], begin_time: float = 0.0, trigger_time: float = 0.0, sample_rate: float = 1e9):
    waveform = SimpleNamespace(
        voltages=voltages, begin_time=begin_time, trigger_time=trigger_time, sample_rate=sample_rate
    )
    return SimpleNamespace(contents=SimpleNamespace(waveforms=[waveform]))


def _capture_with(sdk: MagicMock, scope: SaleaeLogicMSO, exports: dict[str, SimpleNamespace]) -> None:
    sdk.binary_files.read_file.side_effect = lambda path: exports[Path(path).name]
    scope.digitize(timeout=2.0)


def test_open_passes_serial_and_is_idempotent(sdk: MagicMock) -> None:
    driver = SaleaeLogicMSO(serial_number="ABC123")
    driver.open()
    driver.open()
    sdk.MSO.assert_called_once_with(serial_number="ABC123")


def test_open_closes_recorder_when_device_is_missing(sdk: MagicMock, mso: MagicMock) -> None:
    type(mso).part_number = PropertyMock(side_effect=RuntimeError("no device"))
    driver = SaleaeLogicMSO()
    with pytest.raises(RuntimeError, match="no device"):
        driver.open()
    mso.close.assert_called_once_with()
    with pytest.raises(RuntimeError, match="not open"):
        driver.digitize(timeout=1.0)


def test_open_without_sdk_explains_where_to_install_it() -> None:
    with patch.dict(sys.modules, {"saleae": None}), pytest.raises(ImportError, match="downloads.saleae.com/pypi"):
        SaleaeLogicMSO().open()


def test_close_is_idempotent(scope: SaleaeLogicMSO, mso: MagicMock) -> None:
    scope.close()
    scope.close()
    mso.close.assert_called_once_with()


def test_digitize_sends_channel_and_trigger_settings(sdk: MagicMock, mso: MagicMock, scope: SaleaeLogicMSO) -> None:
    scope.set_vertical_scale(0.5, channel=2)
    scope.set_vertical_offset(1.0, channel=2)
    scope.set_probe_attenuation(1, channel=2)
    scope.set_horizontal_scale(1e-4)
    scope.set_trigger_source(2)
    scope.set_trigger_level(1.5)
    scope.set_trigger_slope(TriggerSlope.FALLING)
    _capture_with(sdk, scope, {"analog_0.bin": _export([0.0]), "analog_1.bin": _export([0.0])})

    channel_calls = [c.kwargs for c in sdk.AnalogChannel.call_args_list]
    assert channel_calls == [
        dict(channel=0, center_voltage=0.0, voltage_range=10.0, probe_attenuation=sdk.ProbeAttenuation.PROBE_10X),
        dict(channel=1, center_voltage=1.0, voltage_range=4.0, probe_attenuation=sdk.ProbeAttenuation.PROBE_1X),
    ]
    sdk.EdgeTrigger.assert_called_once_with(
        channel_index=1, threshold_volts=1.5, direction=sdk.EdgeTriggerDirection.FALLING
    )
    sdk.AnalogTriggered.assert_called_once_with(
        trigger=sdk.EdgeTrigger.return_value,
        pre_trigger_seconds=pytest.approx(5e-4),
        post_trigger_seconds=pytest.approx(5e-4),
        timeout_seconds=2.0,
    )
    config_kwargs = sdk.CaptureConfig.call_args.kwargs
    assert config_kwargs["analog_settings"].sample_rate == 1e8
    mso.capture.assert_called_once()
    assert mso.capture.call_args.args[0] is sdk.CaptureConfig.return_value


def test_get_sample_rate_caps_at_device_maximum(scope: SaleaeLogicMSO) -> None:
    scope.set_horizontal_scale(1e-9)
    assert scope.get_sample_rate() == 1.6e9


def test_digitize_maps_capture_timeout(sdk: MagicMock, mso: MagicMock, scope: SaleaeLogicMSO) -> None:
    mso.capture.side_effect = _CaptureTimeoutError()
    with pytest.raises(TimeoutError, match="within 2.0s"):
        scope.digitize(timeout=2.0)


def test_fetch_waveform_times_are_relative_to_trigger(sdk: MagicMock, scope: SaleaeLogicMSO) -> None:
    exports = {
        "analog_0.bin": _export([0.1, 0.2, 0.3], begin_time=10.0, trigger_time=10.000000002),
        "analog_1.bin": _export([0.0]),
    }
    _capture_with(sdk, scope, exports)
    waveform = scope.fetch_waveform(channel=1)
    assert waveform.times == [-2, -1, 0]
    assert waveform.voltages == pytest.approx([0.1, 0.2, 0.3])


def test_fetch_waveform_before_capture_raises(scope: SaleaeLogicMSO) -> None:
    with pytest.raises(RuntimeError, match="digitize"):
        scope.fetch_waveform(channel=1)


def test_single_arms_until_digitize(sdk: MagicMock, scope: SaleaeLogicMSO) -> None:
    scope.single()
    assert scope.get_acquisition_state() == AcquisitionState.RUNNING
    _capture_with(sdk, scope, {"analog_0.bin": _export([0.0]), "analog_1.bin": _export([0.0])})
    assert scope.get_acquisition_state() == AcquisitionState.STOPPED


def test_measure_square_wave(sdk: MagicMock, scope: SaleaeLogicMSO) -> None:
    # 1 kHz, 25% duty, 0..2 V square at 100 kHz: 100 samples per period, 5 periods
    period = [2.0] * 25 + [0.0] * 75
    exports = {"analog_0.bin": _export([0.0] * 10 + period * 5, sample_rate=1e5), "analog_1.bin": _export([0.0])}
    _capture_with(sdk, scope, exports)
    assert scope.measure(ScopeMeasurementType.VPP, channel=1) == 2.0
    assert scope.measure(ScopeMeasurementType.FREQUENCY, channel=1) == pytest.approx(1e3)
    assert scope.measure(ScopeMeasurementType.PERIOD, channel=1) == pytest.approx(1e-3)
    assert scope.measure(ScopeMeasurementType.DUTY_CYCLE, channel=1) == pytest.approx(25.0)


def test_measure_is_nan_without_edges_or_capture(sdk: MagicMock, scope: SaleaeLogicMSO) -> None:
    assert math.isnan(scope.measure(ScopeMeasurementType.VPP, channel=1))
    _capture_with(sdk, scope, {"analog_0.bin": _export([1.0] * 50), "analog_1.bin": _export([0.0])})
    assert scope.measure(ScopeMeasurementType.VAVG, channel=1) == 1.0
    assert math.isnan(scope.measure(ScopeMeasurementType.FREQUENCY, channel=1))


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.set_coupling(Coupling.AC, channel=1),
        lambda s: s.set_trigger_slope(TriggerSlope.EITHER),
        lambda s: s.set_trigger_mode(TriggerMode.AUTO),
        lambda s: s.run(),
    ],
    ids=["ac-coupling", "either-slope", "auto-trigger", "run"],
)
def test_settings_the_api_cannot_apply_raise(scope: SaleaeLogicMSO, call) -> None:
    with pytest.raises(NotImplementedError):
        call(scope)


def test_probe_attenuation_other_than_1x_or_10x_is_rejected(scope: SaleaeLogicMSO) -> None:
    with pytest.raises(ValueError, match="1 or 10"):
        scope.set_probe_attenuation(100, channel=1)
