"""Virtual scope driver: waveforms simulated in-process, with no transport, server, or hardware."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

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

_RECORD_LENGTH = 1000
_HORIZONTAL_DIVS = 10
_VERTICAL_DIVS = 8
# a window spanning more cycles than this is measured over this many, so slow timebases don't alias
_MEASURED_CYCLES = 10
# noise standard deviation, in vertical divisions, before averaging
_NOISE_DIVS = 0.01


@dataclass(frozen=True)
class SineWave:
    """A sine wave at a `VirtualScope` channel's probe tip.

    Attributes:
        frequency (float): Frequency in Hz, not negative. 0 gives a DC level.
        amplitude (float): Peak amplitude in volts (half the peak-to-peak value), not negative.
        offset (float): DC offset in volts.

    Raises:
        ValueError: ``frequency`` or ``amplitude`` is negative or not finite, or ``offset`` is not
            finite.
    """

    frequency: float = 1e3
    amplitude: float = 1.0
    offset: float = 0.0

    def __post_init__(self) -> None:
        """Reject a negative or non-finite frequency or amplitude, or a non-finite offset."""
        _check_signal(self.frequency, self.amplitude, self.offset)

    def _at(self, t: float) -> float:
        """Voltage about the offset at signal time ``t``, in seconds."""
        return self.amplitude * math.sin(2 * math.pi * self.frequency * t)

    def _trigger_phase(self, level: float, slope: TriggerSlope) -> float:
        """Phase, in cycles, where the wave crosses ``level`` (relative to the offset) on ``slope``."""
        crossing = math.asin(max(-1.0, min(1.0, level / self.amplitude))) / (2 * math.pi)
        return 0.5 - crossing if slope == TriggerSlope.FALLING else crossing


@dataclass(frozen=True)
class SquareWave:
    """A 50% duty-cycle square wave at a `VirtualScope` channel's probe tip.

    Each cycle starts with a rising edge from ``offset - amplitude`` to ``offset + amplitude``.
    At 0 Hz it sits at ``offset``, as a 0 Hz `SineWave` does.

    Attributes:
        frequency (float): Frequency in Hz, not negative. 0 gives a DC level.
        amplitude (float): Peak amplitude in volts (half the peak-to-peak value), not negative.
        offset (float): DC offset in volts.

    Raises:
        ValueError: ``frequency`` or ``amplitude`` is negative or not finite, or ``offset`` is not
            finite.
    """

    frequency: float = 1e3
    amplitude: float = 1.0
    offset: float = 0.0

    def __post_init__(self) -> None:
        """Reject a negative or non-finite frequency or amplitude, or a non-finite offset."""
        _check_signal(self.frequency, self.amplitude, self.offset)

    def _at(self, t: float) -> float:
        """Voltage about the offset at signal time ``t``, in seconds."""
        if self.frequency == 0:
            return 0.0
        return self.amplitude if (self.frequency * t) % 1 < 0.5 else -self.amplitude

    def _trigger_phase(self, level: float, slope: TriggerSlope) -> float:
        """Phase, in cycles, of the edge on ``slope``; every edge crosses any level between the rails."""
        return 0.5 if slope == TriggerSlope.FALLING else 0.0


Signal: TypeAlias = SineWave | SquareWave


def _default_signal(channel: int) -> Signal:
    """The signal a `VirtualScope` channel sees unless told otherwise: a sine on odd channels, a square on even."""
    return SineWave() if channel % 2 else SquareWave()


@dataclass
class _Channel:
    vertical_scale: float = 0.5
    vertical_offset: float = 0.0
    coupling: Coupling = Coupling.DC
    probe_attenuation: float = 1.0


@dataclass(frozen=True)
class _Capture:
    waveform: WaveformData
    measured: list[float]
    signal: Signal


class VirtualScope(ScopeDriverBase):
    """Oscilloscope simulated in-process: no transport, server, or hardware.

    Every channel sees a signal from the start: a 1 kHz, 1 V peak `SineWave` on odd channels
    (channel 1, 3, ...) and a 1 kHz, 1 V peak `SquareWave` on even channels (2, 4, ...). A
    capture is 1000 points across 10 horizontal divisions centered on the trigger, where the
    trigger source crosses the trigger level on the configured slope. A sine whose peaks miss the
    level aligns to its nearest peak; a square triggers on its edges at any level. Gaussian noise
    of 0.01 division is added, reduced by the square root of the average count in ``AVERAGE``
    mode, and AC coupling removes the signal's offset. Like an ADC, a capture clips at the screen
    edges: the vertical offset is the voltage at screen center, and the screen spans 4 divisions
    of the vertical scale above and below it.

    While running, every fetch and measurement reads a fresh capture. ``stop``, ``single``, and
    ``digitize`` capture every channel at once, as a real acquisition does, and later fetches and
    measurements read that capture until the scope runs again, whatever settings or signals
    change meanwhile. Voltage measurements come from the same acquisition, clipped the same way,
    but sampled at 100 points per cycle over at most 10 cycles, so a slow timebase reads the
    signal's true levels, as a real scope's full-resolution record does, even when the 1000
    fetched points alias. Frequency, period, and duty cycle come from the signal the capture was
    taken from. Every setting reads back as set.

    It simulates driving a scope from a script or notebook, so it has no screen or storage:
    `save_screenshot`, `save_settings`, and `load_settings` raise ``NotImplementedError``.
    """

    def __init__(self, num_channels: int = 4, signals: Mapping[int, Signal] | None = None) -> None:
        """Create a virtual scope.

        Args:
            num_channels (int): Number of analog-input channels, numbered from 1. Pass the same
                count to `InstroScope`; a channel above it raises ``ValueError``.
            signals (Mapping[int, SineWave | SquareWave] | None): The signal on each listed
                channel. Channels left out see the default: a sine on odd channels, a square on
                even ones. Change a channel's signal at any time with `set_signal`.

        Raises:
            ValueError: ``num_channels`` is less than 1, or ``signals`` names a channel that does
                not exist.

        Example::

            from instro.scope import InstroScope, ScopeMeasurementType
            from instro.scope.drivers import VirtualScope
            from instro.scope.drivers.virtual import SineWave

            driver = VirtualScope(num_channels=4, signals={3: SineWave(frequency=5e3, amplitude=0.5)})
            with InstroScope(name="scope", driver=driver, num_channels=4) as scope:
                scope.single()
                print(scope.measure(ScopeMeasurementType.VRMS, channel=1).latest)  # about 0.71: sine
                print(scope.measure(ScopeMeasurementType.VRMS, channel=2).latest)  # about 1.0: square
                print(scope.measure(ScopeMeasurementType.FREQUENCY, channel=3).latest)  # 5000.0
        """
        if num_channels < 1:
            raise ValueError(f"num_channels must be at least 1, got {num_channels}")
        self.num_channels = num_channels
        self._signals: dict[int, Signal] = {channel: _default_signal(channel) for channel in self._channel_numbers()}
        for channel, signal in (signals or {}).items():
            self.set_signal(signal, channel)
        self._channels = {channel: _Channel() for channel in self._channel_numbers()}
        self._horizontal_scale = 200e-6
        self._acquisition_mode = AcquisitionMode.NORMAL
        self._average_count = 16
        self._trigger_source = 1
        self._trigger_level = 0.0
        self._trigger_slope = TriggerSlope.RISING
        self._state = AcquisitionState.RUNNING
        self._captures: dict[int, _Capture] = {}
        # works around InstroScope calling digitize() on the first read after single() (#716)
        self._single_pending = False

    def set_signal(self, signal: Signal, channel: int) -> None:
        """Set the signal ``channel`` sees; a stopped scope keeps showing its last capture.

        Args:
            signal (SineWave | SquareWave): The signal at the probe tip.
            channel (int): Target channel (1-indexed).

        Raises:
            ValueError: ``channel`` does not exist.
        """
        self._check_channel(channel)
        self._signals[channel] = signal

    def get_signal(self, channel: int) -> Signal:
        """Return the signal ``channel`` sees."""
        self._check_channel(channel)
        return self._signals[channel]

    def open(self) -> None:
        """Nothing to connect to."""

    def close(self) -> None:
        """Nothing to disconnect."""

    def check_errors(self) -> None:
        """No error queue; invalid settings, including NaN or infinite values, raise ``ValueError`` when set."""

    # --- Channel vertical settings ---

    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        self._channel(channel).vertical_scale = _check_positive("vertical scale", volts_per_div)

    def get_vertical_scale(self, channel: int) -> float:
        return self._channel(channel).vertical_scale

    def set_vertical_offset(self, offset: float, channel: int) -> None:
        """Set the voltage at screen center; captures clip 4 divisions above and below it."""
        self._channel(channel).vertical_offset = _check_finite("vertical offset", offset)

    def get_vertical_offset(self, channel: int) -> float:
        return self._channel(channel).vertical_offset

    def set_coupling(self, coupling: Coupling, channel: int) -> None:
        self._channel(channel).coupling = coupling

    def get_coupling(self, channel: int) -> Coupling:
        return self._channel(channel).coupling

    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        """Accepted and read back; waveforms are already referred to the probe tip."""
        self._channel(channel).probe_attenuation = _check_positive("probe attenuation", factor)

    def get_probe_attenuation(self, channel: int) -> float:
        return self._channel(channel).probe_attenuation

    # --- Horizontal (timebase) settings ---

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        self._horizontal_scale = _check_positive("horizontal scale", seconds_per_div)

    def get_horizontal_scale(self) -> float:
        return self._horizontal_scale

    def get_sample_rate(self) -> float:
        """1000 points across the 10-division window."""
        return _RECORD_LENGTH / (_HORIZONTAL_DIVS * self._horizontal_scale)

    # --- Acquisition ---

    def set_acquisition_mode(self, mode: AcquisitionMode) -> None:
        """Accept any mode; only ``AVERAGE`` changes the capture, by reducing its noise."""
        self._acquisition_mode = mode

    def get_acquisition_mode(self) -> AcquisitionMode:
        return self._acquisition_mode

    def set_average_count(self, count: int) -> None:
        if count < 1:
            raise ValueError(f"average count must be at least 1, got {count}")
        self._average_count = count

    def get_average_count(self) -> int:
        return self._average_count

    def run(self) -> None:
        """Free-run: every fetch and measurement reads a fresh capture."""
        self._captures.clear()
        self._single_pending = False
        self._state = AcquisitionState.RUNNING

    def stop(self) -> None:
        """Freeze every channel's capture for later fetches and measurements; a stopped scope keeps its own."""
        self._single_pending = False
        if self._state == AcquisitionState.RUNNING:
            self._acquire()

    def single(self) -> None:
        """Capture every channel at once and stop; the simulated signal always triggers."""
        self._acquire()
        self._single_pending = True

    def digitize(self, timeout: float) -> None:
        """Take one capture and stop; never times out, since the simulated signal always triggers.

        Right after `single`, keeps that shot instead of taking another, to work around an
        `InstroScope` bug (#716).
        """
        if self._single_pending:
            self._single_pending = False
        else:
            self._acquire()

    def get_acquisition_state(self) -> AcquisitionState:
        return self._state

    # --- Waveform data ---

    def fetch_waveform(self, channel: int) -> WaveformData:
        """A copy of the capture, so editing the returned samples leaves later reads unchanged."""
        waveform = self._acquired(channel).waveform
        return WaveformData(times=waveform.times.copy(), voltages=waveform.voltages.copy())

    # --- Measurements ---

    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        """Voltages from the capture; ``NaN`` for frequency, period, and duty cycle on a signal with no edges."""
        capture = self._acquired(channel)
        voltages, signal = capture.measured, capture.signal
        has_edges = signal.frequency > 0 and signal.amplitude > 0
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
            case ScopeMeasurementType.FREQUENCY:
                return signal.frequency if has_edges else math.nan
            case ScopeMeasurementType.PERIOD:
                return 1 / signal.frequency if has_edges else math.nan
            case ScopeMeasurementType.DUTY_CYCLE:
                return 50.0 if has_edges else math.nan

    # --- Trigger ---

    def set_trigger_source(self, channel: int) -> None:
        self._check_channel(channel)
        self._trigger_source = channel

    def set_trigger_type(self, trigger_type: TriggerType) -> None:
        """Accept any type; captures align as an edge trigger."""

    def set_trigger_level(self, level: float) -> None:
        self._trigger_level = _check_finite("trigger level", level)

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        """``EITHER`` aligns on the rising edge."""
        self._trigger_slope = slope

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        """Accept either mode; the simulated signal always triggers."""

    def force_trigger(self) -> None:
        """Nothing to force: the simulated signal always triggers."""

    def get_trigger_status(self) -> TriggerStatus:
        return TriggerStatus.TRIGGERED

    # --- File operations ---

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        """Not supported: the virtual scope has no screen to capture."""
        raise NotImplementedError("VirtualScope has no screen to capture")

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        """Not supported: the virtual scope has no setup storage."""
        raise NotImplementedError("VirtualScope does not save settings")

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        """Not supported: the virtual scope has no setup storage."""
        raise NotImplementedError("VirtualScope does not load settings")

    # --- Simulation ---

    def _channel_numbers(self) -> range:
        return range(1, self.num_channels + 1)

    def _check_channel(self, channel: int) -> None:
        if not 1 <= channel <= self.num_channels:
            raise ValueError(f"VirtualScope has channels 1 to {self.num_channels}, got {channel}")

    def _channel(self, channel: int) -> _Channel:
        self._check_channel(channel)
        return self._channels[channel]

    def _acquire(self) -> None:
        """Capture every channel at once and stop, as a single acquisition does."""
        self._captures = {channel: self._capture(channel) for channel in self._channel_numbers()}
        self._state = AcquisitionState.STOPPED

    def _acquired(self, channel: int) -> _Capture:
        """The capture to read: the frozen one when stopped, a fresh one while running."""
        self._check_channel(channel)
        if self._state == AcquisitionState.STOPPED:
            return self._captures[channel]
        return self._capture(channel)

    def _dc_level(self, channel: int) -> float:
        return 0.0 if self._channel(channel).coupling == Coupling.AC else self._signals[channel].offset

    def _trigger_time(self) -> float:
        """Signal time, in seconds, at which the trigger source crosses the trigger level."""
        signal = self._signals[self._trigger_source]
        if signal.frequency <= 0 or signal.amplitude <= 0:
            return 0.0
        level = self._trigger_level - self._dc_level(self._trigger_source)
        return signal._trigger_phase(level, self._trigger_slope) / signal.frequency

    def _capture(self, channel: int) -> _Capture:
        signal = self._signals[channel]
        window = _HORIZONTAL_DIVS * self._horizontal_scale
        dt = window / _RECORD_LENGTH
        times = [(i - _RECORD_LENGTH // 2) * dt for i in range(_RECORD_LENGTH)]
        voltages = self._sample(channel, times)

        cycles = window * signal.frequency
        if cycles <= _MEASURED_CYCLES:
            measured = voltages
        else:
            span = _MEASURED_CYCLES / signal.frequency
            measured = self._sample(channel, [i * span / _RECORD_LENGTH for i in range(_RECORD_LENGTH)])
        return _Capture(WaveformData(times=[round(t * 1e9) for t in times], voltages=voltages), measured, signal)

    def _sample(self, channel: int, times: list[float]) -> list[float]:
        """Voltages at ``times`` (seconds from the trigger), with noise, clipped at the screen edges."""
        signal = self._signals[channel]
        vertical = self._channel(channel)
        dc_level = self._dc_level(channel)
        averages = self._average_count if self._acquisition_mode == AcquisitionMode.AVERAGE else 1
        noise = _NOISE_DIVS * vertical.vertical_scale / math.sqrt(averages)
        half_screen = _VERTICAL_DIVS / 2 * vertical.vertical_scale
        low, high = vertical.vertical_offset - half_screen, vertical.vertical_offset + half_screen
        t_trigger = self._trigger_time()
        return [min(high, max(low, dc_level + signal._at(t + t_trigger) + random.gauss(0.0, noise))) for t in times]


def _check_signal(frequency: float, amplitude: float, offset: float) -> None:
    if not 0 <= frequency < math.inf:
        raise ValueError(f"frequency must be finite and not negative, got {frequency}")
    if not 0 <= amplitude < math.inf:
        raise ValueError(f"amplitude must be finite and not negative, got {amplitude}")
    if not math.isfinite(offset):
        raise ValueError(f"offset must be finite, got {offset}")


def _check_positive(name: str, value: float) -> float:
    if not 0 < value < math.inf:
        raise ValueError(f"{name} must be positive and finite, got {value}")
    return value


def _check_finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    return value
