"""Arbitrary Waveform Generator (AWG) instrument interface package."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from instro.unstable.awg.awg import (
    AWGDriverBase,
    InstroAWG,
)
from instro.unstable.awg.types import (
    BurstMode,
    Channel,
    ClockSource,
    ModSource,
    ModWaveform,
    OutputPolarity,
    SweepSpacing,
    TriggerSlope,
    TriggerSource,
    VoltageUnit,
    WaveformType,
)

__all__ = [
    "InstroAWG",
    "AWGDriverBase",
    "WaveformType",
    "Channel",
    "ModSource",
    "ModWaveform",
    "SweepSpacing",
    "TriggerSource",
    "TriggerSlope",
    "BurstMode",
    "OutputPolarity",
    "VoltageUnit",
    "ClockSource",
]
