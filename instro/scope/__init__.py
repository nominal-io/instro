"""Oscilloscope instrument interface package."""

from instro.scope.scope import InstroScope, ScopeDriverBase
from instro.scope.types import (
    AcquisitionMode,
    AcquisitionState,
    ChannelConfig,
    Coupling,
    ScopeConfig,
    ScopeMeasurementType,
    TriggerConfig,
    TriggerMode,
    TriggerSlope,
    TriggerStatus,
    TriggerType,
    WaveformData,
)

__all__ = [
    "AcquisitionMode",
    "AcquisitionState",
    "ChannelConfig",
    "Coupling",
    "InstroScope",
    "ScopeConfig",
    "ScopeDriverBase",
    "ScopeMeasurementType",
    "TriggerConfig",
    "TriggerMode",
    "TriggerSlope",
    "TriggerStatus",
    "TriggerType",
    "WaveformData",
]
