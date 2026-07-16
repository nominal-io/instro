from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from instro.lib.transports.visa import VisaConfig
from instro.lib.types import DeviceInfo

if TYPE_CHECKING:
    from instro.lib.publishers import Publisher
    from instro.psu.psu import InstroPSU, PSUDriverBase

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

    poll_interval: float = Field(ge=0.01, le=10.0, description="Polling interval in seconds")


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
    format: Literal["json", "csv", "avro"] = "avro"
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


def build_psu_from_config(
    config: PSUConfig,
    publishers: list[Publisher] | None = None,
) -> InstroPSU:
    """Construct an InstroPSU from a validated PSUConfig."""
    import importlib

    from instro.psu.psu import InstroPSU

    module_path, class_name = PSU_VENDOR_REGISTRY[config.driver.name].rsplit(".", 1)
    driver_cls = getattr(importlib.import_module(module_path), class_name)

    driver: PSUDriverBase = driver_cls(config.driver.visa)  # type: ignore[call-arg]

    all_publishers = list(publishers or [])
    all_publishers.extend(_build_publisher(p) for p in config.publishers)

    psu = InstroPSU(
        name=config.device.name,
        driver=driver,
        num_channels=config.driver.num_channels,
        publishers=all_publishers or None,
    )
    if config.timing is not None:
        psu.background_interval = config.timing.poll_interval
    return psu
