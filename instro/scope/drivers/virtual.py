"""Virtual scope driver: waveforms simulated in-process, with no transport, server, or hardware."""

from __future__ import annotations

import json
import math
import random
import struct
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
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
# noise standard deviation, in vertical divisions, before averaging
_NOISE_DIVS = 0.01

_DIV_PX = 50
_BACKGROUND = (0, 0, 0)
_GRATICULE = (64, 64, 64)
_TRACE_COLORS = ((255, 230, 0), (0, 220, 255), (255, 0, 200), (60, 120, 255))


@dataclass(frozen=True)
class SineWave:
    """A sine wave at a `VirtualScope` channel's probe tip.

    Attributes:
        frequency (float): Frequency in Hz, not negative. 0 gives a DC level.
        amplitude (float): Peak amplitude in volts (half the peak-to-peak value), not negative.
        offset (float): DC offset in volts.

    Raises:
        ValueError: ``frequency`` or ``amplitude`` is negative or not finite.
    """

    frequency: float = 1e3
    amplitude: float = 1.0
    offset: float = 0.0

    def __post_init__(self) -> None:
        """Reject a negative or non-finite frequency or amplitude."""
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

    Attributes:
        frequency (float): Frequency in Hz, not negative. 0 gives a DC level.
        amplitude (float): Peak amplitude in volts (half the peak-to-peak value), not negative.
        offset (float): DC offset in volts.

    Raises:
        ValueError: ``frequency`` or ``amplitude`` is negative or not finite.
    """

    frequency: float = 1e3
    amplitude: float = 1.0
    offset: float = 0.0

    def __post_init__(self) -> None:
        """Reject a negative or non-finite frequency or amplitude."""
        _check_signal(self.frequency, self.amplitude, self.offset)

    def _at(self, t: float) -> float:
        """Voltage about the offset at signal time ``t``, in seconds."""
        if self.frequency == 0:
            return self.amplitude
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


@dataclass
class _Trigger:
    source: int = 1
    type: TriggerType = TriggerType.EDGE
    level: float = 0.0
    slope: TriggerSlope = TriggerSlope.RISING
    mode: TriggerMode = TriggerMode.AUTO


@dataclass
class _Settings:
    channels: dict[int, _Channel] = field(default_factory=dict)
    horizontal_scale: float = 200e-6
    acquisition_mode: AcquisitionMode = AcquisitionMode.NORMAL
    average_count: int = 16
    trigger: _Trigger = field(default_factory=_Trigger)


class VirtualScope(ScopeDriverBase):
    """Oscilloscope simulated in-process: no transport, server, or hardware.

    Every channel sees a signal from the start: a 1 kHz, 1 V peak `SineWave` on odd channels
    (channel 1, 3, ...) and a 1 kHz, 1 V peak `SquareWave` on even channels (2, 4, ...). A
    capture is 1000 points across 10 horizontal divisions centered on the trigger, where the
    trigger source crosses the trigger level on the configured slope. A sine whose peaks miss the
    level aligns to its nearest peak; a square triggers on its edges at any level. Gaussian noise
    of 0.01 division is added, reduced by the square root of the average count in ``AVERAGE``
    mode, and AC coupling removes the signal's offset.

    While running, every fetch and measurement reads a fresh capture. ``stop``, ``single``, and
    ``digitize`` capture every channel at once, as a real acquisition does, and later fetches and
    measurements read that capture until the scope runs again, whatever settings or signals
    change meanwhile. Voltage measurements are computed from the capture; frequency, period, and
    duty cycle come from the signal it was taken from. Every setting reads back as set, and setups
    saved with ``to_instrument=True`` stay in memory for the life of the driver.
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
        self._settings = _Settings(channels={channel: _Channel() for channel in self._channel_numbers()})
        self._state = AcquisitionState.RUNNING
        self._captures: dict[int, tuple[WaveformData, Signal]] = {}
        self._storage: dict[str, bytes] = {}

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
        """No error queue; invalid settings raise ``ValueError`` when set."""

    # --- Channel vertical settings ---

    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        if volts_per_div <= 0:
            raise ValueError(f"vertical scale must be positive, got {volts_per_div}")
        self._channel(channel).vertical_scale = volts_per_div

    def get_vertical_scale(self, channel: int) -> float:
        return self._channel(channel).vertical_scale

    def set_vertical_offset(self, offset: float, channel: int) -> None:
        self._channel(channel).vertical_offset = offset

    def get_vertical_offset(self, channel: int) -> float:
        return self._channel(channel).vertical_offset

    def set_coupling(self, coupling: Coupling, channel: int) -> None:
        self._channel(channel).coupling = coupling

    def get_coupling(self, channel: int) -> Coupling:
        return self._channel(channel).coupling

    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        """Accepted and read back; waveforms are already referred to the probe tip."""
        if not factor > 0:
            raise ValueError(f"probe attenuation must be positive, got {factor}")
        self._channel(channel).probe_attenuation = factor

    def get_probe_attenuation(self, channel: int) -> float:
        return self._channel(channel).probe_attenuation

    # --- Horizontal (timebase) settings ---

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        if seconds_per_div <= 0:
            raise ValueError(f"horizontal scale must be positive, got {seconds_per_div}")
        self._settings.horizontal_scale = seconds_per_div

    def get_horizontal_scale(self) -> float:
        return self._settings.horizontal_scale

    def get_sample_rate(self) -> float:
        """1000 points across the 10-division window."""
        return _RECORD_LENGTH / (_HORIZONTAL_DIVS * self._settings.horizontal_scale)

    # --- Acquisition ---

    def set_acquisition_mode(self, mode: AcquisitionMode) -> None:
        """Accept any mode; only ``AVERAGE`` changes the capture, by reducing its noise."""
        self._settings.acquisition_mode = mode

    def get_acquisition_mode(self) -> AcquisitionMode:
        return self._settings.acquisition_mode

    def set_average_count(self, count: int) -> None:
        if count < 1:
            raise ValueError(f"average count must be at least 1, got {count}")
        self._settings.average_count = count

    def get_average_count(self) -> int:
        return self._settings.average_count

    def run(self) -> None:
        """Free-run: every fetch and measurement reads a fresh capture."""
        self._captures.clear()
        self._state = AcquisitionState.RUNNING

    def stop(self) -> None:
        """Freeze every channel's capture for later fetches and measurements; a stopped scope keeps its own."""
        if self._state == AcquisitionState.RUNNING:
            self._acquire()

    def single(self) -> None:
        """Capture every channel at once and stop; the simulated signal always triggers."""
        self._acquire()

    def digitize(self, timeout: float) -> None:
        """Take one capture and stop; never times out, since the simulated signal always triggers."""
        self.single()

    def get_acquisition_state(self) -> AcquisitionState:
        return self._state

    # --- Waveform data ---

    def fetch_waveform(self, channel: int) -> WaveformData:
        return self._acquired(channel)[0]

    # --- Measurements ---

    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        """Voltages from the capture; ``NaN`` for frequency, period, and duty cycle on a signal with no edges."""
        waveform, signal = self._acquired(channel)
        voltages = waveform.voltages
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
        self._settings.trigger.source = channel

    def set_trigger_type(self, trigger_type: TriggerType) -> None:
        """Accept any type; captures align as an edge trigger."""
        self._settings.trigger.type = trigger_type

    def set_trigger_level(self, level: float) -> None:
        self._settings.trigger.level = level

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        """``EITHER`` aligns on the rising edge."""
        self._settings.trigger.slope = slope

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        """Accept either mode; the simulated signal always triggers."""
        self._settings.trigger.mode = mode

    def force_trigger(self) -> None:
        """Nothing to force: the simulated signal always triggers."""

    def get_trigger_status(self) -> TriggerStatus:
        return TriggerStatus.TRIGGERED

    # --- File operations ---

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        """Render a PNG of the screen: the graticule and a trace per channel."""
        return self._save(filepath, self._render_png(), to_instrument)

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        """Save every setting as JSON."""
        settings = self._settings
        setup = {
            "channels": {
                str(ch): {
                    "vertical_scale": c.vertical_scale,
                    "vertical_offset": c.vertical_offset,
                    "coupling": c.coupling.value,
                    "probe_attenuation": c.probe_attenuation,
                }
                for ch, c in settings.channels.items()
            },
            "horizontal_scale": settings.horizontal_scale,
            "acquisition_mode": settings.acquisition_mode.value,
            "average_count": settings.average_count,
            "trigger": {
                "source": settings.trigger.source,
                "type": settings.trigger.type.value,
                "level": settings.trigger.level,
                "slope": settings.trigger.slope.value,
                "mode": settings.trigger.mode.value,
            },
        }
        return self._save(name, json.dumps(setup, indent=2).encode(), to_instrument)

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        """Restore settings saved by `save_settings`; channels the setup leaves out keep their defaults.

        Raises:
            FileNotFoundError: No setup named ``name`` was saved on the virtual scope.
            ValueError: The setup names a channel or trigger source that does not exist.
        """
        if from_instrument:
            if name not in self._storage:
                raise FileNotFoundError(f"no setup named {name!r} saved on the virtual scope")
            data = self._storage[name]
        else:
            data = Path(name).read_bytes()
        setup = json.loads(data)
        trigger = setup["trigger"]
        channels = {channel: _Channel() for channel in self._channel_numbers()}
        for ch, c in setup["channels"].items():
            self._check_channel(int(ch))
            channels[int(ch)] = _Channel(
                c["vertical_scale"], c["vertical_offset"], Coupling(c["coupling"]), c["probe_attenuation"]
            )
        self._check_channel(trigger["source"])
        self._settings = _Settings(
            channels=channels,
            horizontal_scale=setup["horizontal_scale"],
            acquisition_mode=AcquisitionMode(setup["acquisition_mode"]),
            average_count=setup["average_count"],
            trigger=_Trigger(
                trigger["source"],
                TriggerType(trigger["type"]),
                trigger["level"],
                TriggerSlope(trigger["slope"]),
                TriggerMode(trigger["mode"]),
            ),
        )

    # --- Simulation ---

    def _channel_numbers(self) -> range:
        return range(1, self.num_channels + 1)

    def _check_channel(self, channel: int) -> None:
        if not 1 <= channel <= self.num_channels:
            raise ValueError(f"VirtualScope has channels 1 to {self.num_channels}, got {channel}")

    def _channel(self, channel: int) -> _Channel:
        self._check_channel(channel)
        return self._settings.channels[channel]

    def _acquire(self) -> None:
        """Capture every channel at once and stop, as a single acquisition does."""
        self._captures = {
            channel: (self._capture(channel), self._signals[channel]) for channel in self._channel_numbers()
        }
        self._state = AcquisitionState.STOPPED

    def _acquired(self, channel: int) -> tuple[WaveformData, Signal]:
        """The capture to read: the frozen one when stopped, a fresh one while running."""
        self._check_channel(channel)
        if self._state == AcquisitionState.STOPPED:
            return self._captures[channel]
        return self._capture(channel), self._signals[channel]

    def _dc_level(self, channel: int) -> float:
        return 0.0 if self._channel(channel).coupling == Coupling.AC else self._signals[channel].offset

    def _trigger_time(self) -> float:
        """Signal time, in seconds, at which the trigger source crosses the trigger level."""
        trigger = self._settings.trigger
        signal = self._signals[trigger.source]
        if signal.frequency <= 0 or signal.amplitude <= 0:
            return 0.0
        level = trigger.level - self._dc_level(trigger.source)
        return signal._trigger_phase(level, trigger.slope) / signal.frequency

    def _capture(self, channel: int) -> WaveformData:
        settings = self._settings
        signal = self._signals[channel]
        dc_level = self._dc_level(channel)
        averages = settings.average_count if settings.acquisition_mode == AcquisitionMode.AVERAGE else 1
        noise = _NOISE_DIVS * self._channel(channel).vertical_scale / math.sqrt(averages)
        t_trigger = self._trigger_time()
        dt = _HORIZONTAL_DIVS * settings.horizontal_scale / _RECORD_LENGTH
        times = [(i - _RECORD_LENGTH // 2) * dt for i in range(_RECORD_LENGTH)]
        voltages = [dc_level + signal._at(t + t_trigger) + random.gauss(0.0, noise) for t in times]
        return WaveformData(times=[round(t * 1e9) for t in times], voltages=voltages)

    def _save(self, name: str, data: bytes, to_instrument: bool) -> bytes:
        if to_instrument:
            self._storage[name] = data
            return b""
        Path(name).write_bytes(data)
        return data

    def _render_png(self) -> bytes:
        width, height = _HORIZONTAL_DIVS * _DIV_PX, _VERTICAL_DIVS * _DIV_PX
        pixels = [
            [_GRATICULE if x % _DIV_PX == 0 or y % _DIV_PX == 0 else _BACKGROUND for x in range(width)]
            for y in range(height)
        ]
        for channel in self._channel_numbers():
            color = _TRACE_COLORS[(channel - 1) % len(_TRACE_COLORS)]
            settings = self._channel(channel)
            voltages = self._acquired(channel)[0].voltages
            previous_y: int | None = None
            for i, v in enumerate(voltages):
                x = i * width // len(voltages)
                divs = (v - settings.vertical_offset) / settings.vertical_scale
                y = min(height - 1, max(0, round(height / 2 - divs * _DIV_PX)))
                low, high = sorted((y, y if previous_y is None else previous_y))
                for row in range(low, high + 1):
                    pixels[row][x] = color
                previous_y = y
        raw = b"".join(b"\x00" + bytes(c for pixel in row for c in pixel) for row in pixels)
        return (
            b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(raw))
            + _png_chunk(b"IEND", b"")
        )


def _check_signal(frequency: float, amplitude: float, offset: float) -> None:
    if not 0 <= frequency < math.inf:
        raise ValueError(f"frequency must be finite and not negative, got {frequency}")
    if not 0 <= amplitude < math.inf:
        raise ValueError(f"amplitude must be finite and not negative, got {amplitude}")
    if not math.isfinite(offset):
        raise ValueError(f"offset must be finite, got {offset}")


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
