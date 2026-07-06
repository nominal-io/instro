"""Oscilloscope shared types: coupling, acquisition/trigger modes, waveform data, tracked config."""

from dataclasses import dataclass, field
from enum import Enum


class Coupling(Enum):
    """Input coupling mode for an oscilloscope channel."""

    AC = "AC"
    DC = "DC"


class AcquisitionMode(Enum):
    """Oscilloscope acquisition mode."""

    NORMAL = "NORMAL"
    AVERAGE = "AVERAGE"
    HIGH_RESOLUTION = "HIGH_RESOLUTION"
    PEAK_DETECT = "PEAK_DETECT"
    ENVELOPE = "ENVELOPE"


class TriggerType(Enum):
    """Oscilloscope trigger type."""

    EDGE = "EDGE"
    PULSE = "PULSE"


class TriggerSlope(Enum):
    """Trigger edge slope."""

    RISING = "RISING"
    FALLING = "FALLING"
    EITHER = "EITHER"


class TriggerMode(Enum):
    """Trigger mode controlling how the oscilloscope waits for a trigger."""

    AUTO = "AUTO"
    NORMAL = "NORMAL"


class TriggerStatus(Enum):
    """Read-back trigger status."""

    ARMED = "ARMED"
    READY = "READY"
    TRIGGERED = "TRIGGERED"
    AUTO = "AUTO"
    SAVE = "SAVE"
    SCAN = "SCAN"


class AcquisitionState(Enum):
    """Oscilloscope acquisition run state."""

    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


class ScopeMeasurementType(Enum):
    """Built-in oscilloscope measurement types."""

    VPP = "VPP"
    VMAX = "VMAX"
    VMIN = "VMIN"
    VAVG = "VAVG"
    VRMS = "VRMS"
    FREQUENCY = "FREQUENCY"
    PERIOD = "PERIOD"
    DUTY_CYCLE = "DUTY_CYCLE"


@dataclass
class WaveformData:
    """Raw waveform from a fetch: ``times`` in ns relative to the trigger; ``voltages`` after probe attenuation."""

    times: list[int]
    voltages: list[float]


@dataclass
class ChannelConfig:
    """Tracked per-channel state. Fields start as ``None`` and populate on set/query."""

    vertical_scale: float | None = None
    vertical_offset: float | None = None
    coupling: Coupling | None = None
    probe_attenuation: float | None = None


@dataclass
class TriggerConfig:
    """Tracked trigger state (source channel, type, level (V), slope, mode). Fields start as ``None``."""

    source: int | None = None
    type: TriggerType | None = None
    level: float | None = None
    slope: TriggerSlope | None = None
    mode: TriggerMode | None = None


@dataclass
class ScopeConfig:
    """Tracked scope state: per-channel (1-based), trigger, acquisition mode, average count, timebase."""

    channels: dict[int, ChannelConfig] = field(default_factory=dict)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    acquisition_mode: AcquisitionMode | None = None
    average_count: int | None = None
    horizontal_scale: float | None = None
