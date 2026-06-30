from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from instro.lib.publishers import Publisher
    from instro.psu.psu import InstroPSU, PSUDriverBase

PSU_VENDOR_REGISTRY: dict[str, str] = {
    "bk_9115": "instro.psu.drivers.bk_9115.BK9115",
    "bk_914x": "instro.psu.drivers.bk_914x.BK914X",
    "keysight_e36100": "instro.psu.drivers.keysight_e36100.KeysightE36100",
    "keysight_n5700": "instro.psu.drivers.keysight_n5700.KeysightN5700",
    "rigol_dp800": "instro.psu.drivers.rigol_dp800.RigolDP800",
    "siglent_spd3303": "instro.psu.drivers.siglent_spd3303.SiglentSPD3303",
    "simulated": "instro.psu.drivers.simulated.SimulatedPSU",
    "tdk_lambda_genesys": "instro.psu.drivers.tdk_lambda_genesys.TDKLambdaGenesys",
}


class PSUConfig(BaseModel):
    """Validated config for constructing an InstroPSU from JSON."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Channel-name prefix for published data.")
    vendor: str = Field(description="PSU vendor/model key.")
    connection: str = Field(description="VISA resource string (e.g 'USB0::...' or 'TCPIP0::...').")
    num_channels: int = Field(ge=1, description="Number of output channels.")
    visa_backend: str | None = Field(
        default=None, description="pyvisa backend specifier, defaults to @ivi and falls back to @py."
    )
    dataset_rid: str | None = Field(default=None, description="Nominal dataset RID for auto-publishing.")
    output_directory: str | None = Field(
        default=None, description="Directory path for writing output data to a local file."
    )

    @field_validator("vendor")
    @classmethod
    def vendor_must_be_registered(cls, v: str) -> str:
        if v not in PSU_VENDOR_REGISTRY:
            raise ValueError(f"unknown vendor {v!r}; valid: {sorted(PSU_VENDOR_REGISTRY)}")
        return v


def build_psu_from_config(
    config: PSUConfig,
    publishers: list[Publisher] | None = None,
) -> InstroPSU:
    """Construct an InstroPSU from a validated PSUConfig."""
    import importlib

    from instro.lib.transports.visa import VisaConfig
    from instro.psu.psu import InstroPSU

    module_path, class_name = PSU_VENDOR_REGISTRY[config.vendor].rsplit(".", 1)
    driver_cls = getattr(importlib.import_module(module_path), class_name)

    visa_config = VisaConfig(visa_resource=config.connection, visa_backend=config.visa_backend)

    driver: PSUDriverBase = driver_cls(visa_config)  # type: ignore[call-arg]

    from instro.lib.publishers import FilePublisher, NominalCorePublisher

    all_publishers = list(publishers or [])
    if config.dataset_rid is not None:
        all_publishers.append(NominalCorePublisher(config.dataset_rid))
    if config.output_directory is not None:
        all_publishers.append(FilePublisher(directory=config.output_directory))

    return InstroPSU(
        name=config.name,
        driver=driver,
        num_channels=config.num_channels,
        publishers=all_publishers or None,
    )
