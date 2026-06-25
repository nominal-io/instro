"""Signal generator shared types and enumerations."""

# Leave the types up to 

from enum import Enum

# Start without ARB since ARB can theoretically be an infinite number of waves added together.
# Starting with fully defined waveforms then adding ARB after single waveforms are fully implemented.
class WaveformType(Enum):
    SINE = "SINE"
    SQUARE = "SQUARE"
    RAMP = "RAMP"
    PULSE = "PULSE"
    NOISE = "NOISE"
    DC = "DC"
    ARB = "USER"


# Start with 2 channels since Rigol DG1022 has 2 channels. More can be added if needed in the future.
class Channel(Enum):
    CH1 = 1
    CH2 = 2


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
