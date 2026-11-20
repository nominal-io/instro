"""Saleae Logic MSO driver, over Saleae's ``saleae-mso-api`` Python package (analog channels only)."""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from instro.scope import ScopeDriverBase
from instro.scope.types import (
    AcquisitionMode,
    AcquisitionState,
    Coupling,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerStatus,
    TriggerType,
    WaveformData,
)

_INSTALL_HINT = (
    "SaleaeLogicMSO needs Saleae's saleae-mso-api package, which is published on Saleae's own index, "
    "not PyPI: pip install saleae-mso-api --extra-index-url https://downloads.saleae.com/pypi"
)

# The MSO API has no per-division settings; these map the scope contract's V/div and s/div onto
# the API's full-scale voltage range and capture window.
_VERTICAL_DIVS = 8
_HORIZONTAL_DIVS = 10

# Samples per channel the sample rate is chosen to give over the capture window, before the
# device's maximum rate and downsample steps limit it.
_TARGET_RECORD_LENGTH = 100_000

# Hysteresis band around the mid level, as a fraction of Vpp, so noise near the mid level
# doesn't count as extra edges.
_EDGE_HYSTERESIS = 0.1

_PROBE_ATTENUATIONS = (1.0, 10.0)


@dataclass
class _ChannelSettings:
    """Per-channel settings sent with each capture; defaults match the API's ``AnalogChannel`` defaults."""

    volts_per_div: float = 10.0 / _VERTICAL_DIVS
    offset: float = 0.0
    probe_attenuation: float = 10.0


@dataclass(frozen=True)
class _Waveform:
    """One channel of the last capture: trigger-relative times (ns), volts, and sample period (s)."""

    times: list[int]
    voltages: list[float]
    sample_period: float


class SaleaeLogicMSO(ScopeDriverBase):
    """Driver for the Saleae Logic MSO (2- and 4-channel analog), using Saleae's ``saleae-mso-api``."""

    def __init__(self, serial_number: str | None = None) -> None:
        """Create a Logic MSO driver. Nothing touches the device until :meth:`open`.

        The API only takes blocking captures, so :meth:`single` arms and the capture itself runs in
        :meth:`digitize`, which ``InstroScope`` calls when ``fetch_waveform`` or ``measure`` reads an
        armed scope. The driver holds every channel and trigger setting and sends them with each
        capture; all analog channels are captured every time.

        Args:
            serial_number (str | None): Serial number of the MSO to use. ``None`` uses the first
                MSO found.

        Example::

            from instro.scope import InstroScope
            from instro.unstable.scope.drivers import SaleaeLogicMSO

            with InstroScope(name="mso", driver=SaleaeLogicMSO(), num_channels=4) as scope:
                scope.set_trigger_source(1)
                scope.set_trigger_level(1.5)
                scope.single()
                waveform = scope.fetch_waveform(channel=1, timeout=5.0)  # captures here
        """
        self._serial_number = serial_number
        self._sdk: Any = None
        self._binary_files: Any = None
        self._mso: Any = None
        self._channels: dict[int, _ChannelSettings] = {}
        self._horizontal_scale = 1e-4
        self._trigger_source = 1
        self._trigger_level = 0.0
        self._trigger_slope = TriggerSlope.RISING
        self._waveforms: dict[int, _Waveform] = {}
        self._armed = False

    def open(self) -> None:
        """Start the API's recorder process and read the device's part number to confirm it's connected."""
        if self._mso is not None:
            return
        try:
            from saleae import mso_api  # type: ignore[import-not-found]
            from saleae.mso_api import binary_files  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(_INSTALL_HINT) from exc
        mso = mso_api.MSO(serial_number=self._serial_number)
        try:
            mso.part_number
        except Exception:
            mso.close()
            raise
        self._sdk = mso_api
        self._binary_files = binary_files
        self._mso = mso

    def close(self) -> None:
        if self._mso is not None:
            self._mso.close()
            self._mso = None

    def check_errors(self) -> None:
        """No-op: the API has no error queue and raises on each failed call instead."""

    # --- Channel vertical settings ---

    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        """Set the full-scale range sent to the device to ``volts_per_div`` × 8 divisions."""
        if volts_per_div <= 0:
            raise ValueError(f"Vertical scale must be positive, got {volts_per_div}")
        self._channel(channel).volts_per_div = volts_per_div

    def get_vertical_scale(self, channel: int) -> float:
        return self._channel(channel).volts_per_div

    def set_vertical_offset(self, offset: float, channel: int) -> None:
        """Set the voltage at the center of ``channel``'s range (the API's ``center_voltage``)."""
        self._channel(channel).offset = offset

    def get_vertical_offset(self, channel: int) -> float:
        return self._channel(channel).offset

    def set_coupling(self, coupling: Coupling, channel: int) -> None:
        """Accept DC only: ``saleae-mso-api`` doesn't send a coupling setting to the device."""
        if coupling != Coupling.DC:
            raise NotImplementedError("Saleae Logic MSO driver supports DC coupling only")

    def get_coupling(self, channel: int) -> Coupling:
        return Coupling.DC

    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        """Set 1x or 10x, the only attenuations the API accepts."""
        if factor not in _PROBE_ATTENUATIONS:
            raise ValueError(f"Saleae Logic MSO supports probe attenuation 1 or 10, got {factor}")
        self._channel(channel).probe_attenuation = float(factor)

    def get_probe_attenuation(self, channel: int) -> float:
        return self._channel(channel).probe_attenuation

    # --- Horizontal (timebase) settings ---

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        """Set the capture window to ``seconds_per_div`` × 10 divisions, centered on the trigger."""
        if seconds_per_div <= 0:
            raise ValueError(f"Horizontal scale must be positive, got {seconds_per_div}")
        self._horizontal_scale = seconds_per_div

    def get_horizontal_scale(self) -> float:
        return self._horizontal_scale

    # --- Sample rate ---

    def get_sample_rate(self) -> float:
        """Rate the next capture uses: 100,000 samples over the window, capped at the device maximum, snapped to a supported rate."""
        return float(self._analog_settings().sample_rate)

    # --- Acquisition ---

    def set_acquisition_mode(self, mode: AcquisitionMode) -> None:
        if mode != AcquisitionMode.NORMAL:
            raise NotImplementedError(f"{mode.value} acquisition mode is not supported on Saleae Logic MSO")

    def get_acquisition_mode(self) -> AcquisitionMode:
        return AcquisitionMode.NORMAL

    def set_average_count(self, count: int) -> None:
        raise NotImplementedError("Saleae Logic MSO has no averaging acquisition mode")

    def get_average_count(self) -> int:
        return 1

    def run(self) -> None:
        raise NotImplementedError("Saleae Logic MSO has no free-running mode; use digitize()")

    def stop(self) -> None:
        """Disarm a pending :meth:`single`; captures run to completion inside :meth:`digitize`."""
        self._armed = False

    def single(self) -> None:
        """Arm a capture without starting it; the next :meth:`digitize` runs it."""
        self._armed = True

    def digitize(self, timeout: float) -> None:
        """Capture every analog channel, triggered on the trigger source, and keep the data for readout.

        Raises:
            TimeoutError: No trigger within ``timeout`` seconds.
        """
        mso = self._require_open()
        self._armed = False
        sdk = self._sdk
        half_window = self._horizontal_scale * _HORIZONTAL_DIVS / 2
        num_channels = mso.part_number.num_analog_channels
        channels = []
        for ch in range(1, num_channels + 1):
            settings = self._channel(ch)
            channels.append(
                sdk.AnalogChannel(
                    channel=ch - 1,
                    center_voltage=settings.offset,
                    voltage_range=settings.volts_per_div * _VERTICAL_DIVS,
                    probe_attenuation=(
                        sdk.ProbeAttenuation.PROBE_1X
                        if settings.probe_attenuation == 1
                        else sdk.ProbeAttenuation.PROBE_10X
                    ),
                )
            )
        direction = (
            sdk.EdgeTriggerDirection.FALLING
            if self._trigger_slope == TriggerSlope.FALLING
            else sdk.EdgeTriggerDirection.RISING
        )
        trigger = sdk.EdgeTrigger(
            channel_index=self._trigger_source - 1, threshold_volts=self._trigger_level, direction=direction
        )
        config = sdk.CaptureConfig(
            enabled_channels=channels,
            analog_settings=self._analog_settings(),
            capture_settings=sdk.AnalogTriggered(
                trigger=trigger,
                pre_trigger_seconds=half_window,
                post_trigger_seconds=half_window,
                # the API rejects a trigger timeout shorter than the pre-trigger window
                timeout_seconds=max(timeout, half_window),
            ),
        )
        # The API writes each capture to disk; the data is copied out so the directory can go.
        with tempfile.TemporaryDirectory(prefix="instro-saleae-", ignore_cleanup_errors=True) as tmp:
            save_dir = Path(tmp)
            try:
                mso.capture(config, save_dir)
            except sdk.exceptions.CaptureTimeoutError as exc:
                raise TimeoutError(
                    f"Acquisition did not complete within {timeout}s. The trigger condition may not have been met."
                ) from exc
            self._waveforms = {
                ch: self._read_waveform(save_dir / f"analog_{ch - 1}.bin") for ch in range(1, num_channels + 1)
            }

    def get_acquisition_state(self) -> AcquisitionState:
        """RUNNING while :meth:`single` is armed and its capture hasn't run, otherwise STOPPED."""
        return AcquisitionState.RUNNING if self._armed else AcquisitionState.STOPPED

    # --- Waveform data ---

    def fetch_waveform(self, channel: int) -> WaveformData:
        """Return ``channel`` from the last :meth:`digitize`, with times in ns relative to the trigger."""
        waveform = self._waveforms.get(channel)
        if waveform is None:
            raise RuntimeError(f"No captured data for channel {channel}; call digitize() first")
        return WaveformData(times=list(waveform.times), voltages=list(waveform.voltages))

    # --- Measurements ---

    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        """Compute ``measurement_type`` on the host from the last capture; ``NaN`` with no capture or too few edges."""
        waveform = self._waveforms.get(channel)
        if waveform is None or not waveform.voltages:
            return math.nan
        voltages = waveform.voltages
        match measurement_type:
            case ScopeMeasurementType.VPP:
                return max(voltages) - min(voltages)
            case ScopeMeasurementType.VMAX:
                return max(voltages)
            case ScopeMeasurementType.VMIN:
                return min(voltages)
            case ScopeMeasurementType.VAVG:
                return sum(voltages) / len(voltages)
            case ScopeMeasurementType.VRMS:
                return math.sqrt(sum(v * v for v in voltages) / len(voltages))
        rising = _rising_edges(voltages)
        if len(rising) < 2:
            return math.nan
        period = (rising[-1] - rising[0]) / (len(rising) - 1) * waveform.sample_period
        match measurement_type:
            case ScopeMeasurementType.FREQUENCY:
                return 1 / period
            case ScopeMeasurementType.PERIOD:
                return period
            case ScopeMeasurementType.DUTY_CYCLE:
                mid = (max(voltages) + min(voltages)) / 2
                cycles = voltages[rising[0] : rising[-1]]
                return 100.0 * sum(1 for v in cycles if v > mid) / len(cycles)
        raise NotImplementedError(f"{measurement_type.value} is not supported on Saleae Logic MSO")

    # --- Trigger ---

    def set_trigger_source(self, channel: int) -> None:
        self._trigger_source = channel

    def set_trigger_type(self, trigger_type: TriggerType) -> None:
        if trigger_type != TriggerType.EDGE:
            raise NotImplementedError(f"{trigger_type.value} trigger is not supported on Saleae Logic MSO")

    def set_trigger_level(self, level: float) -> None:
        self._trigger_level = level

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        if slope == TriggerSlope.EITHER:
            raise NotImplementedError("Saleae Logic MSO triggers on RISING or FALLING edges only")
        self._trigger_slope = slope

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        """Accept NORMAL only: a capture waits for its trigger until the ``digitize`` timeout."""
        if mode != TriggerMode.NORMAL:
            raise NotImplementedError(f"{mode.value} trigger mode is not supported on Saleae Logic MSO")

    def force_trigger(self) -> None:
        raise NotImplementedError("Saleae Logic MSO has no force trigger")

    def get_trigger_status(self) -> TriggerStatus:
        raise NotImplementedError("Saleae Logic MSO does not report trigger status")

    # --- File operations ---

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        raise NotImplementedError("Saleae Logic MSO has no screen to capture")

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        raise NotImplementedError("Saleae Logic MSO has no settings storage")

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        raise NotImplementedError("Saleae Logic MSO has no settings storage")

    # --- Helpers ---

    def _channel(self, channel: int) -> _ChannelSettings:
        return self._channels.setdefault(channel, _ChannelSettings())

    def _require_open(self) -> Any:
        if self._mso is None:
            raise RuntimeError("SaleaeLogicMSO is not open; call open() first")
        return self._mso

    def _analog_settings(self) -> Any:
        """``AnalogSettings`` for the current window, its sample rate snapped by the API to one the device supports."""
        part_number = self._require_open().part_number
        window = self._horizontal_scale * _HORIZONTAL_DIVS
        rate = min(part_number.max_analog_sample_rate_hz, _TARGET_RECORD_LENGTH / window)
        settings = self._sdk.AnalogSettings(sample_rate=rate)
        settings.validate_and_update_sample_rate_for(part_number)
        return settings

    def _read_waveform(self, path: Path) -> _Waveform:
        """Read one ``analog_<n>.bin`` export, which carries the trigger time ``Capture`` drops."""
        waveform = self._binary_files.read_file(path).contents.waveforms[0]
        sample_period = 1 / float(waveform.sample_rate)
        begin = float(waveform.begin_time) - float(waveform.trigger_time)
        voltages = [float(v) for v in waveform.voltages]
        times = [round((begin + i * sample_period) * 1e9) for i in range(len(voltages))]
        return _Waveform(times=times, voltages=voltages, sample_period=sample_period)


def _rising_edges(voltages: list[float]) -> list[int]:
    """Sample indices where ``voltages`` rises through the upper hysteresis threshold after dropping below the lower."""
    vmax, vmin = max(voltages), min(voltages)
    mid = (vmax + vmin) / 2
    band = (vmax - vmin) * _EDGE_HYSTERESIS
    high, low = mid + band, mid - band
    edges = []
    armed = voltages[0] < low
    for i, v in enumerate(voltages):
        if armed and v > high:
            edges.append(i)
            armed = False
        elif v < low:
            armed = True
    return edges
