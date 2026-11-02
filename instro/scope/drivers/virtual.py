"""Virtual scope driver: waveforms simulated in-process, with no transport, server, or hardware."""

from __future__ import annotations

import json
import math
import random
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

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
    """The signal a `VirtualScope` channel sees at the probe tip.

    Attributes:
        frequency (float): Frequency in Hz. 0 gives a DC level.
        amplitude (float): Peak amplitude in volts (half the peak-to-peak value).
        offset (float): DC offset in volts.
    """

    frequency: float = 1e3
    amplitude: float = 1.0
    offset: float = 0.0


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

    Each channel sees a `SineWave` (1 kHz, 1 V peak by default). A capture is 1000 points
    across 10 horizontal divisions centered on the trigger, where the trigger source crosses
    the trigger level on the configured slope (or reaches its nearest peak when the level is
    out of range). Gaussian noise of 0.01 division is added, reduced by the square root of
    the average count in ``AVERAGE`` mode, and AC coupling removes the signal's offset.
    Voltage measurements are computed from the capture; frequency, period, and duty cycle
    come from the signal. Every setting reads back as set, and setups saved with
    ``to_instrument=True`` stay in memory for the life of the driver.
    """

    def __init__(self, signals: dict[int, SineWave] | None = None) -> None:
        """Create a virtual scope.

        Args:
            signals (dict[int, SineWave] | None): The signal on each 1-based channel. Channels
                left out see the default ``SineWave()``: 1 kHz, 1 V peak, no offset.

        Example::

            from instro.scope import InstroScope, ScopeMeasurementType
            from instro.scope.drivers import VirtualScope
            from instro.scope.drivers.virtual import SineWave

            driver = VirtualScope({2: SineWave(frequency=5e3, amplitude=0.5)})
            with InstroScope(name="scope", driver=driver, num_channels=2) as scope:
                waveform = scope.fetch_waveform(channel=1)
                print(scope.measure(ScopeMeasurementType.FREQUENCY, channel=2).latest)  # 5000.0
        """
        self.signals: dict[int, SineWave] = dict(signals or {})
        self._settings = _Settings()
        self._state = AcquisitionState.RUNNING
        self._captures: dict[int, WaveformData] = {}
        self._storage: dict[str, bytes] = {}

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
        self._state = AcquisitionState.RUNNING

    def stop(self) -> None:
        """Freeze the last capture for later fetches and measurements."""
        self._state = AcquisitionState.STOPPED

    def single(self) -> None:
        """Take one capture and stop; the simulated signal always triggers."""
        self._captures.clear()
        self._state = AcquisitionState.STOPPED

    def digitize(self, timeout: float) -> None:
        """Take one capture and stop; never times out, since the simulated signal always triggers."""
        self.single()

    def get_acquisition_state(self) -> AcquisitionState:
        return self._state

    # --- Waveform data ---

    def fetch_waveform(self, channel: int) -> WaveformData:
        return self._acquired(channel)

    # --- Measurements ---

    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        """Voltages from the capture; ``NaN`` for frequency, period, and duty cycle on a signal with no edges."""
        voltages = self._acquired(channel).voltages
        signal = self._signal(channel)
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
        self._channel(channel)
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
        """Render a PNG of the screen: the graticule and a trace per channel used so far."""
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
        """Restore settings saved by `save_settings`."""
        if from_instrument:
            if name not in self._storage:
                raise FileNotFoundError(f"no setup named {name!r} saved on the virtual scope")
            data = self._storage[name]
        else:
            data = Path(name).read_bytes()
        setup = json.loads(data)
        trigger = setup["trigger"]
        self._settings = _Settings(
            channels={
                int(ch): _Channel(
                    c["vertical_scale"], c["vertical_offset"], Coupling(c["coupling"]), c["probe_attenuation"]
                )
                for ch, c in setup["channels"].items()
            },
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

    def _channel(self, channel: int) -> _Channel:
        if channel < 1:
            raise ValueError(f"channel must be 1 or greater, got {channel}")
        return self._settings.channels.setdefault(channel, _Channel())

    def _signal(self, channel: int) -> SineWave:
        return self.signals.get(channel, SineWave())

    def _acquired(self, channel: int) -> WaveformData:
        if self._state == AcquisitionState.RUNNING or channel not in self._captures:
            self._captures[channel] = self._capture(channel)
        return self._captures[channel]

    def _dc_level(self, channel: int) -> float:
        return 0.0 if self._channel(channel).coupling == Coupling.AC else self._signal(channel).offset

    def _trigger_time(self) -> float:
        """Signal time, in seconds, at which the trigger source crosses the trigger level."""
        trigger = self._settings.trigger
        signal = self._signal(trigger.source)
        if signal.frequency <= 0 or signal.amplitude <= 0:
            return 0.0
        crossing = (trigger.level - self._dc_level(trigger.source)) / signal.amplitude
        phase = math.asin(max(-1.0, min(1.0, crossing)))
        if trigger.slope == TriggerSlope.FALLING:
            phase = math.pi - phase
        return phase / (2 * math.pi * signal.frequency)

    def _capture(self, channel: int) -> WaveformData:
        settings = self._settings
        signal = self._signal(channel)
        dc_level = self._dc_level(channel)
        averages = settings.average_count if settings.acquisition_mode == AcquisitionMode.AVERAGE else 1
        noise = _NOISE_DIVS * self._channel(channel).vertical_scale / math.sqrt(averages)
        t_trigger = self._trigger_time()
        dt = _HORIZONTAL_DIVS * settings.horizontal_scale / _RECORD_LENGTH
        times = [(i - _RECORD_LENGTH // 2) * dt for i in range(_RECORD_LENGTH)]
        voltages = [
            dc_level
            + signal.amplitude * math.sin(2 * math.pi * signal.frequency * (t + t_trigger))
            + random.gauss(0.0, noise)
            for t in times
        ]
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
        for channel in sorted(self._settings.channels.keys() | self.signals.keys()) or [1]:
            color = _TRACE_COLORS[(channel - 1) % len(_TRACE_COLORS)]
            settings = self._channel(channel)
            voltages = self._acquired(channel).voltages
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


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
