from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.config import (
    FilePublisherConfig,
    NominalCorePublisherConfig,
    PublisherConfigType,
    TimingConfig,
    build_publisher,
)
from instro.lib.transports.visa import VisaConfig
from instro.lib.types import DeviceInfo

if TYPE_CHECKING:
    from instro.eload.eload import ELoadDriverBase
    from instro.lib.publishers import Publisher

__all__ = [
    "DeviceInfo",
    "ELoadConfig",
    "FilePublisherConfig",
    "LoadConfig",
    "NominalCorePublisherConfig",
    "SlewRateConfig",
    "TimingConfig",
    "VisaDriverConfig",
]

ELOAD_VENDOR_REGISTRY: dict[str, str] = {
    "BK85XXB": "instro.eload.drivers.bk_85xxb.BK85XXB",
}


class SlewRateConfig(BaseModel):
    """Per-edge current slew rate, applied through ``set_slewrate``."""

    model_config = ConfigDict(extra="forbid")
    direction: SlewRateDirection = Field(description="Edge(s) the rate applies to: 'RISE', 'FALL', or 'BOTH'.")
    rate: float = Field(description="Slew rate in amperes per microsecond (A/µs).")


class LoadConfig(BaseModel):
    """Initial load state, applied through the InstroELoad setters on ``open()``. Never enables the input."""

    model_config = ConfigDict(extra="forbid")
    mode: LoadMode = Field(description="Operating mode: 'CC', 'CV', 'CP', or 'CR'.")
    level: float | None = Field(
        default=None,
        description="Operating level in the mode's units (CC: A, CV: V, CP: W, CR: Ω); omit to keep the instrument default.",
    )
    range: float | None = Field(
        default=None,
        description="Operating range in the mode's units; omit to keep the instrument default.",
    )
    slew_rate: SlewRateConfig | None = Field(default=None, description="Per-edge current slew rate.")


class VisaDriverConfig(BaseModel):
    """Driver config for a VISA-connected E-Load."""

    model_config = ConfigDict(extra="forbid")
    connection_type: Literal["visa"] = "visa"
    name: str = Field(description="E-Load vendor/model key.")
    visa: VisaConfig

    @field_validator("name")
    @classmethod
    def name_must_be_registered(cls, v: str) -> str:
        if v not in ELOAD_VENDOR_REGISTRY:
            raise ValueError(f"unknown driver {v!r}")
        return v


class ELoadConfig(BaseModel):
    """Validated config for constructing an InstroELoad from JSON."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    instrument: Literal["InstroELoad"] = "InstroELoad"
    device: DeviceInfo
    driver: VisaDriverConfig
    load: LoadConfig | None = None
    timing: TimingConfig | None = None
    publishers: list[PublisherConfigType] = Field(default_factory=list)


def resolve_eload_from_config(
    config: ELoadConfig,
) -> tuple[str, ELoadDriverBase, list[Publisher], float | None]:
    """Resolve a validated ELoadConfig into the ``(name, driver, config_publishers, poll_interval)`` InstroELoad needs."""
    import importlib

    module_path, class_name = ELOAD_VENDOR_REGISTRY[config.driver.name].rsplit(".", 1)
    driver_cls = getattr(importlib.import_module(module_path), class_name)
    driver: ELoadDriverBase = driver_cls(config.driver.visa)  # type: ignore[call-arg]

    config_publishers = [build_publisher(p) for p in config.publishers]
    poll_interval = config.timing.poll_interval if config.timing is not None else None
    return config.device.name, driver, config_publishers, poll_interval
