"""Signal generator shared types and enumerations."""

from dataclasses import dataclass
from enum import Enum


class WaveformType(Enum):
    SINE = "SINE"
    SQUARE = "SQUARE"
    RAMP = "RAMP"
    PULSE = "PULSE"
    NOISE = "NOISE"
    DC = "DC"


class VoltageUnit(Enum):
    VPP = "VPP"
    VRMS = "VRMS"
    DBM = "DBM"


@dataclass
class AWGChannelConfig:
    """Tracks the last-commanded configuration of an AWG channel; None means not yet commanded."""

    waveform: WaveformType
    voltage_unit: VoltageUnit | None = None
    output_enabled: bool | None = None
    frequency_hz: float | None = None
