"""Signal generator shared types and enumerations."""

from enum import Enum


class WaveformType(Enum):
    SINE = "SINE"
    SQUARE = "SQUARE"
    RAMP = "RAMP"
    PULSE = "PULSE"
    NOISE = "NOISE"
    DC = "DC"
    ARB = "USER"


class ModSource(Enum):
    INTERNAL = "INT"
    EXTERNAL = "EXT"


class ModWaveform(Enum):
    SINE = "SINE"
    SQUARE = "SQUARE"
    RAMP = "RAMP"
    NEG_RAMP = "NRAM"
    TRIANGLE = "TRI"
    NOISE = "NOISE"
    ARB = "USER"


class SweepSpacing(Enum):
    LINEAR = "LIN"
    LOGARITHMIC = "LOG"


class TriggerSource(Enum):
    INTERNAL = "IMM"  # DG1022 uses IMMediate, not INT
    EXTERNAL = "EXT"
    MANUAL = "BUS"


class TriggerSlope(Enum):
    POSITIVE = "POS"
    NEGATIVE = "NEG"


class BurstMode(Enum):
    TRIGGERED = "TRIG"
    GATED = "GAT"


class OutputPolarity(Enum):
    NORMAL = "NORM"
    INVERTED = "INV"


class VoltageUnit(Enum):
    VPP = "VPP"
    VRMS = "VRMS"
    DBM = "DBM"


class ClockSource(Enum):
    INTERNAL = "INT"
    EXTERNAL = "EXT"
