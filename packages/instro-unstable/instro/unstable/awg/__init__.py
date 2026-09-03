"""Arbitrary Waveform Generator (AWG) instrument interface package."""

from instro.unstable.awg.awg import (
    AWGDriverBase,
    InstroAWG,
)
from instro.unstable.awg.config import AWGConfig
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    BurstTriggerSource,
    BurstType,
    GatePolarity,
    ModulationType,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    SweepTriggerSource,
    SweepType,
    Triangle,
    Waveform,
    convert_amplitude,
)

__all__ = [
    "InstroAWG",
    "AWGConfig",
    "AWGDriverBase",
    "Waveform",
    "Sine",
    "Square",
    "Sawtooth",
    "Triangle",
    "Pulse",
    "Arbitrary",
    "StaticValue",
    "AmplitudeMeasurementUnit",
    "ModulationType",
    "BurstType",
    "BurstTriggerSource",
    "GatePolarity",
    "SweepType",
    "SweepTriggerSource",
    "convert_amplitude",
]
