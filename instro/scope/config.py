"""Pydantic config models for JSON config-driven ``InstroScope`` construction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from instro.lib.config import (
    FilePublisherConfig,
    NominalCorePublisherConfig,
    PublisherConfigType,
    TimingConfig,
    build_publisher,
)
from instro.lib.transports.visa import VisaConfig
from instro.lib.types import DeviceInfo
from instro.scope.types import (
    AcquisitionMode,
    Coupling,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerType,
)

if TYPE_CHECKING:
    from instro.lib.publishers import Publisher
    from instro.scope.scope import ScopeDriverBase

__all__ = [
    "AcquisitionConfig",
    "ChannelConfig",
    "DeviceInfo",
    "FilePublisherConfig",
    "NominalCorePublisherConfig",
    "ScopeConfig",
    "TimingConfig",
    "TriggerConfig",
    "VisaDriverConfig",
]

SCOPE_VENDOR_REGISTRY: dict[str, str] = {
    "Keysight1200X": "instro.scope.drivers.keysight_1200x.Keysight1200X",
    "SiglentSDS1000XE": "instro.scope.drivers.siglent_sds1000x_e.SiglentSDS1000XE",
    "Tektronix2SeriesMSO": "instro.scope.drivers.tektronix_2series.Tektronix2SeriesMSO",
}


class ChannelConfig(BaseModel):
    """Initial per-channel state and polled measurements, applied through the InstroScope setters on ``open()``."""

    model_config = ConfigDict(extra="forbid")
    vertical_scale: float | None = Field(default=None, gt=0, description="Vertical scale in V/div.")
    vertical_offset: float | None = Field(default=None, description="Vertical offset in volts.")
    coupling: Coupling | None = Field(default=None, description="Input coupling, 'AC' or 'DC'.")
    probe_attenuation: float | None = Field(default=None, gt=0, description="Probe attenuation ratio, e.g. 1, 10, 100.")
    measurements: list[ScopeMeasurementType] = Field(
        default_factory=list, description="Built-in measurements to background-poll on this channel."
    )

    @field_validator("measurements")
    @classmethod
    def _no_duplicate_measurements(cls, v: list[ScopeMeasurementType]) -> list[ScopeMeasurementType]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate measurement types in a channel's measurements list")
        return v


class AcquisitionConfig(BaseModel):
    """Initial acquisition state, applied through the InstroScope setters on ``open()``."""

    model_config = ConfigDict(extra="forbid")
    mode: AcquisitionMode | None = Field(default=None, description="Acquisition mode, e.g. 'NORMAL' or 'AVERAGE'.")
    average_count: int | None = Field(default=None, ge=2, description="Waveforms to average; requires mode 'AVERAGE'.")
    horizontal_scale: float | None = Field(default=None, gt=0, description="Timebase in s/div.")
    start_acquisition_on_open: bool = Field(
        default=False, description="Start continuous acquisition (``run()``) as the last step of ``open()``."
    )

    @model_validator(mode="after")
    def _average_count_requires_average_mode(self) -> AcquisitionConfig:
        if self.average_count is not None and self.mode != AcquisitionMode.AVERAGE:
            raise ValueError("average_count requires mode 'AVERAGE'")
        return self


class TriggerConfig(BaseModel):
    """Initial trigger state, applied through the InstroScope setters on ``open()``."""

    model_config = ConfigDict(extra="forbid")
    source: int = Field(ge=1, description="1-based analog channel used as the trigger source.")
    type: TriggerType | None = Field(default=None, description="Trigger type, 'EDGE' or 'PULSE'.")
    level: float | None = Field(default=None, description="Trigger level in volts.")
    slope: TriggerSlope | None = Field(default=None, description="Trigger slope, 'RISING', 'FALLING', or 'EITHER'.")
    mode: TriggerMode | None = Field(default=None, description="Trigger sweep mode, 'AUTO' or 'NORMAL'.")


class VisaDriverConfig(BaseModel):
    """Driver config for a VISA-connected scope."""

    model_config = ConfigDict(extra="forbid")
    connection_type: Literal["visa"] = "visa"
    name: str = Field(description="Scope vendor/model key.")
    num_channels: int = Field(ge=1, description="Number of analog-input channels.")
    visa: VisaConfig

    @field_validator("name")
    @classmethod
    def name_must_be_registered(cls, v: str) -> str:
        if v not in SCOPE_VENDOR_REGISTRY:
            raise ValueError(f"unknown driver {v!r}")
        return v


class ScopeConfig(BaseModel):
    """Validated config for constructing an InstroScope from JSON."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    instrument: Literal["InstroScope"] = "InstroScope"
    device: DeviceInfo
    driver: VisaDriverConfig
    channels: dict[int, ChannelConfig] = Field(
        default_factory=dict, description="Per-channel initial state keyed by 1-based channel number."
    )
    acquisition: AcquisitionConfig | None = None
    trigger: TriggerConfig | None = None
    timing: TimingConfig | None = None
    publishers: list[PublisherConfigType] = Field(default_factory=list)

    @model_validator(mode="after")
    def _channels_within_num_channels(self) -> ScopeConfig:
        num_channels = self.driver.num_channels
        out_of_range = sorted(ch for ch in self.channels if not 1 <= ch <= num_channels)
        if out_of_range:
            raise ValueError(f"channels {out_of_range} are outside 1..{num_channels} (driver.num_channels)")
        if self.trigger is not None and self.trigger.source > num_channels:
            raise ValueError(f"trigger.source {self.trigger.source} is outside 1..{num_channels} (driver.num_channels)")
        return self

    @model_validator(mode="after")
    def _timing_requires_measurements(self) -> ScopeConfig:
        if self.timing is not None and not any(ch.measurements for ch in self.channels.values()):
            raise ValueError(
                "timing requires at least one channel with a non-empty measurements list: "
                "background polling has nothing to do otherwise"
            )
        return self


def resolve_scope_from_config(
    config: ScopeConfig,
) -> tuple[str, ScopeDriverBase, int, list[Publisher], float | None]:
    """Resolve a validated ScopeConfig into the ``(name, driver, num_channels, config_publishers, poll_interval)`` InstroScope needs."""
    import importlib

    module_path, class_name = SCOPE_VENDOR_REGISTRY[config.driver.name].rsplit(".", 1)
    driver_cls = getattr(importlib.import_module(module_path), class_name)
    driver: ScopeDriverBase = driver_cls(config.driver.visa)  # type: ignore[call-arg]

    config_publishers = [build_publisher(p) for p in config.publishers]
    poll_interval = config.timing.poll_interval if config.timing is not None else None
    return config.device.name, driver, config.driver.num_channels, config_publishers, poll_interval
