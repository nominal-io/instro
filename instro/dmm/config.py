from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from instro.dmm.types import MeasurementFunction
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
    from instro.dmm.dmm import DMMDriverBase
    from instro.lib.publishers import Publisher

__all__ = [
    "DMMConfig",
    "DeviceInfo",
    "FilePublisherConfig",
    "MeasurementConfig",
    "NominalCorePublisherConfig",
    "TimingConfig",
    "VisaDriverConfig",
]

DMM_VENDOR_REGISTRY: dict[str, str] = {
    "Agilent34401A": "instro.dmm.drivers.agilent_a34401a.Agilent34401A",
    "Keithley2400": "instro.dmm.drivers.keithley_2400.Keithley2400",
    "Keysight34461A": "instro.dmm.drivers.keysight_34461a.Keysight34461A",
    "SimulatedDMM": "instro.dmm.drivers.simulated.SimulatedDMM",
}


class MeasurementConfig(BaseModel):
    """Initial measurement state, applied through the InstroDMM setters on ``open()``."""

    model_config = ConfigDict(extra="forbid")
    function: MeasurementFunction = Field(description="Measurement function, e.g. 'DC_VOLTAGE'.")
    digits: int | None = Field(default=None, description="Resolution in digits; omit to keep the instrument default.")
    aperture_nplc: float | None = Field(default=None, description="Integration time in power-line cycles.")
    aperture_seconds: float | None = Field(default=None, description="Integration time in seconds.")
    range: float | Literal["auto"] | None = Field(
        default=None,
        description="Manual range in the function's units, or 'auto'; omit to keep the instrument default.",
    )

    @model_validator(mode="after")
    def _aperture_mutually_exclusive(self) -> MeasurementConfig:
        if self.aperture_nplc is not None and self.aperture_seconds is not None:
            raise ValueError("aperture_nplc and aperture_seconds are mutually exclusive")
        return self


class VisaDriverConfig(BaseModel):
    """Driver config for a VISA-connected DMM."""

    model_config = ConfigDict(extra="forbid")
    connection_type: Literal["visa"] = "visa"
    name: str = Field(description="DMM vendor/model key.")
    visa: VisaConfig

    @field_validator("name")
    @classmethod
    def name_must_be_registered(cls, v: str) -> str:
        if v not in DMM_VENDOR_REGISTRY:
            raise ValueError(f"unknown driver {v!r}")
        return v


class DMMConfig(BaseModel):
    """Validated config for constructing an InstroDMM from JSON."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    instrument: Literal["InstroDMM"] = "InstroDMM"
    device: DeviceInfo
    driver: VisaDriverConfig
    measurement: MeasurementConfig | None = None
    timing: TimingConfig | None = None
    publishers: list[PublisherConfigType] = Field(default_factory=list)

    @model_validator(mode="after")
    def _timing_requires_measurement(self) -> DMMConfig:
        if self.timing is not None and self.measurement is None:
            raise ValueError(
                "timing requires a measurement block: background polling can't start without a measurement function"
            )
        return self


def resolve_dmm_from_config(
    config: DMMConfig,
    publishers: list[Publisher] | None = None,
) -> tuple[str, DMMDriverBase, list[Publisher] | None, float | None]:
    """Resolve a validated DMMConfig into the ``(name, driver, publishers, poll_interval)`` InstroDMM needs."""
    import importlib

    module_path, class_name = DMM_VENDOR_REGISTRY[config.driver.name].rsplit(".", 1)
    driver_cls = getattr(importlib.import_module(module_path), class_name)
    driver: DMMDriverBase = driver_cls(config.driver.visa)  # type: ignore[call-arg]

    all_publishers = list(publishers or [])
    all_publishers.extend(build_publisher(p) for p in config.publishers)

    poll_interval = config.timing.poll_interval if config.timing is not None else None
    return config.device.name, driver, (all_publishers or None), poll_interval
