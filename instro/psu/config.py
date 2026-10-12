from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from instro.lib.config import (
    FilePublisherConfig,
    NominalCorePublisherConfig,
    PublisherConfigType,
    TimingConfig,
    build_publisher,
)
from instro.lib.registry import DriverEntry, IdnPattern
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

PSU_VENDOR_REGISTRY: dict[str, DriverEntry] = {
    "BK9115": DriverEntry("instro.psu.drivers.bk_9115.BK9115", (IdnPattern(("B&K PRECISION",), r"^9115", 1),)),
    "BK914X": DriverEntry("instro.psu.drivers.bk_914x.BK914X", (IdnPattern(("B&K PRECISION",), r"^914\d", 3),)),
    # E36100, N5700 and Genesys patterns follow the programming manuals; not yet confirmed on hardware.
    "KeysightE36100": DriverEntry(
        "instro.psu.drivers.keysight_e36100.KeysightE36100",
        (IdnPattern(("KEYSIGHT TECHNOLOGIES", "AGILENT TECHNOLOGIES"), r"^E361\d\d", 1),),
    ),
    "KeysightN5700": DriverEntry(
        "instro.psu.drivers.keysight_n5700.KeysightN5700",
        (IdnPattern(("KEYSIGHT TECHNOLOGIES", "AGILENT TECHNOLOGIES"), r"^N57\d\d", 1),),
    ),
    "RigolDP800": DriverEntry(
        "instro.psu.drivers.rigol_dp800.RigolDP800",
        (
            IdnPattern(("RIGOL TECHNOLOGIES",), r"^DP811", 1),
            IdnPattern(("RIGOL TECHNOLOGIES",), r"^DP821", 2),
            IdnPattern(("RIGOL TECHNOLOGIES",), r"^DP83[12]", 3),
        ),
    ),
    # CH3 has no SCPI voltage/current access, so discovery reports 2 programmable channels (#406).
    "SiglentSPD3303": DriverEntry(
        "instro.psu.drivers.siglent_spd3303.SiglentSPD3303",
        (IdnPattern(("SIGLENT TECHNOLOGIES",), r"^SPD3303", 2),),
    ),
    "SimulatedPSU": DriverEntry("instro.psu.drivers.simulated.SimulatedPSU"),
    "TDKLambdaGenesys": DriverEntry(
        "instro.psu.drivers.tdk_lambda_genesys.TDKLambdaGenesys",
        (IdnPattern(("LAMBDA",), r"^GEN", 1),),
    ),
}


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


class PSUConfig(BaseModel):
    """Validated config for constructing an InstroPSU from JSON."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    instrument: Literal["InstroPSU"] = "InstroPSU"
    device: DeviceInfo
    driver: VisaDriverConfig
    timing: TimingConfig | None = None
    publishers: list[PublisherConfigType] = Field(default_factory=list)


def resolve_psu_from_config(
    config: PSUConfig,
) -> tuple[str, PSUDriverBase, int, list[Publisher], float | None]:
    """Resolve a validated PSUConfig into the ``(name, driver, num_channels, config_publishers, poll_interval)`` InstroPSU needs."""
    driver_cls = PSU_VENDOR_REGISTRY[config.driver.name].load()
    driver: PSUDriverBase = driver_cls(config.driver.visa)  # type: ignore[call-arg]

    config_publishers = [build_publisher(p) for p in config.publishers]
    poll_interval = config.timing.poll_interval if config.timing is not None else None
    return config.device.name, driver, config.driver.num_channels, config_publishers, poll_interval
