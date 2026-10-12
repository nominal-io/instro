"""Oscilloscope instrument interface package."""

from instro.scope.config import (
    AcquisitionConfig,
    ChannelConfig,
    ScopeConfig,
    TriggerConfig,
    VisaDriverConfig,
)
from instro.scope.scope import InstroScope, ScopeDriverBase
from instro.scope.types import (
    AcquisitionMode,
    AcquisitionState,
    ChannelState,
    Coupling,
    ScopeMeasurementType,
    ScopeState,
    TriggerMode,
    TriggerSlope,
    TriggerState,
    TriggerStatus,
    TriggerType,
    WaveformData,
)

__all__ = [
    "AcquisitionConfig",
    "AcquisitionMode",
    "AcquisitionState",
    "ChannelConfig",
    "ChannelState",
    "Coupling",
    "InstroScope",
    "ScopeConfig",
    "ScopeDriverBase",
    "ScopeMeasurementType",
    "ScopeState",
    "TriggerConfig",
    "TriggerMode",
    "TriggerSlope",
    "TriggerState",
    "TriggerStatus",
    "TriggerType",
    "VisaDriverConfig",
    "WaveformData",
]
