"""Signal generator shared types and enumerations."""

from enum import Enum


class WaveformType(Enum):
    SINE = "SIN"
    SQUARE = "SQU"
    RAMP = "RAMP"
    PULSE = "PULS"
    NOISE = "NOIS"
    DC = "DC"
    ARB = "USER"


class Channel(Enum):
    CH1 = 1
    CH2 = 2


class ModSource(Enum):
    INTERNAL = "INT"
    EXTERNAL = "EXT"


class ModWaveform(Enum):
    SINE = "SIN"
    SQUARE = "SQU"
    RAMP = "RAMP"
    NEG_RAMP = "NRAM"
    TRIANGLE = "TRI"
    NOISE = "NOIS"
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
