from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from instro.lib.transports.visa import VisaConfig
from instro.lib.types import DeviceInfo

if TYPE_CHECKING:
    from instro.lib.publishers import Publisher
    from instro.psu.psu import PSUDriverBase

__all__ = [
    "DeviceInfo",
    "FilePublisherConfig",
    "NominalCorePublisherConfig",
    "PSUConfig",
    "TimingConfig",
    "VisaDriverConfig",
]

PSU_VENDOR_REGISTRY: dict[str, str] = {
    "BK9115": "instro.psu.drivers.bk_9115.BK9115",
    "BK914X": "instro.psu.drivers.bk_914x.BK914X",
    "KeysightE36100": "instro.psu.drivers.keysight_e36100.KeysightE36100",
    "KeysightN5700": "instro.psu.drivers.keysight_n5700.KeysightN5700",
    "RigolDP800": "instro.psu.drivers.rigol_dp800.RigolDP800",
    "SiglentSPD3303": "instro.psu.drivers.siglent_spd3303.SiglentSPD3303",
    "SimulatedPSU": "instro.psu.drivers.simulated.SimulatedPSU",
    "TDKLambdaGenesys": "instro.psu.drivers.tdk_lambda_genesys.TDKLambdaGenesys",
}


class TimingConfig(BaseModel):
    """Timing configuration for PSU background polling."""

    model_config = ConfigDict(extra="forbid")
    poll_interval: float = Field(ge=0.01, description="Polling interval in seconds")


class VisaDriverConfig(BaseModel):
    """Driver config for a VISA-connected PSU."""

    model_config = ConfigDict(extra="forbid")
    connection_type: Literal["visa"] = "visa"
    name: str = Field(description="PSU vendor/model key.")
    num_channels: int = Field(ge=1, description="Number of output channels.")
    visa: VisaConfig

    @field_validator("name")
    @classmethod
    def name_must_be_registered(cls, v: str) -> str:
        if v not in PSU_VENDOR_REGISTRY:
            raise ValueError(f"unknown driver {v!r}")
        return v


class NominalCorePublisherConfig(BaseModel):
    """Publisher config for streaming to a Nominal Core dataset."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["NominalCorePublisher"] = "NominalCorePublisher"
    dataset_rid: str = Field(description="Target Nominal Core dataset RID.")
    batch_size: int | None = Field(default=None, description="Publish batch size override.")
    profile: str | None = Field(
        default=None, description="On-disk Nominal credential profile name; defaults to 'default'."
    )


class FilePublisherConfig(BaseModel):
    """Publisher config for writing measurements to a local file."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["FilePublisher"] = "FilePublisher"
    directory: str = Field(description="Output directory for the written file.")
    format: Literal["json", "jsonl", "csv", "avro"] = "avro"
    custom_file_name: str | None = Field(default=None, description="Filename without extension.")


PublisherConfigType = Annotated[NominalCorePublisherConfig | FilePublisherConfig, Field(discriminator="type")]


class PSUConfig(BaseModel):
    """Validated config for constructing an InstroPSU from JSON."""

    model_config = ConfigDict(extra="forbid")
    version: int = 1
    instrument: Literal["InstroPSU"] = "InstroPSU"
    device: DeviceInfo
    driver: VisaDriverConfig
    timing: TimingConfig | None = None
    publishers: list[PublisherConfigType] = Field(default_factory=list)


def _build_publisher(config: NominalCorePublisherConfig | FilePublisherConfig) -> Publisher:
    from instro.lib.publishers import FilePublisher, NominalCorePublisher

    if isinstance(config, NominalCorePublisherConfig):
        return NominalCorePublisher(
            dataset_rid=config.dataset_rid, batch_size=config.batch_size, profile=config.profile
        )
    return FilePublisher(directory=config.directory, format=config.format, custom_file_name=config.custom_file_name)


def resolve_psu_from_config(
    config: PSUConfig,
    publishers: list[Publisher] | None = None,
) -> tuple[str, PSUDriverBase, int, list[Publisher] | None, float | None]:
    """Resolve a validated PSUConfig into the ``(name, driver, num_channels, publishers, poll_interval)`` InstroPSU needs."""
    import importlib

    module_path, class_name = PSU_VENDOR_REGISTRY[config.driver.name].rsplit(".", 1)
    driver_cls = getattr(importlib.import_module(module_path), class_name)
    driver: PSUDriverBase = driver_cls(config.driver.visa)  # type: ignore[call-arg]

    all_publishers = list(publishers or [])
    all_publishers.extend(_build_publisher(p) for p in config.publishers)

    poll_interval = config.timing.poll_interval if config.timing is not None else None
    return config.device.name, driver, config.driver.num_channels, (all_publishers or None), poll_interval
