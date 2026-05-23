"""Oscilloscope instrument interface package."""

from instro.unstable.scope.driver import ScopeDriverBase
from instro.unstable.scope.scope import InstroScope
from instro.unstable.scope.types import (
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
