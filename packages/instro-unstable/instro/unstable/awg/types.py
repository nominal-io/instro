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
    ARB = "USER"


class VoltageUnit(Enum):
    VPP = "VPP"
    VRMS = "VRMS"
    DBM = "DBM"


@dataclass
class AWGChannelConfig:
    """Tracks the waveform an AWG channel is currently configured for."""

    waveform: WaveformType
