from __future__ import annotations

from typing import TYPE_CHECKING, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from instro.lib.publishers import Publisher
    from instro.psu.psu import InstroPSU, PSUDriverBase

PSUVendor = Literal[
    "bk_9115",
    "bk_914x",
    "keysight_e36100",
    "keysight_n5700",
    "rigol_dp800",
    "siglent_spd3303",
    "simulated",
    "tdk_lambda_genesys",
]


class PSUConfig(BaseModel):
    """Validated config for constructing an InstroPSU from JSON."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Channel-name prefix for published data.")
    vendor: PSUVendor = Field(description="PSU vendor/model key.")
    connection: str = Field(description="VISA resource string (e.g 'USB0::...' or 'TCPIP0::...').")
    num_channels: int = Field(ge=1, description="Number of output channels.")
    visa_backend: str | None = Field(
        default=None, description="pyvisa backend specifier, defaults to @ivi and falls back to @py."
    )
    dataset_rid: str | None = Field(default=None, description="Nominal dataset RID for auto-publishing.")
    output_directory: str | None = Field(
        default=None, description="Directory path for writing output data to a local file."
    )


def build_psu_from_config(
    config: PSUConfig,
    publishers: list[Publisher] | None = None,
) -> InstroPSU:
    """Construct an InstroPSU from a validated PSUConfig."""
    from instro.lib.transports.visa import VisaConfig
    from instro.psu.drivers import (
        BK914X,
        BK9115,
        KeysightE36100,
        KeysightN5700,
        RigolDP800,
        SiglentSPD3303,
        SimulatedPSU,
        TDKLambdaGenesys,
    )
    from instro.psu.psu import InstroPSU

    _registry: dict[PSUVendor, type[PSUDriverBase]] = {
        "bk_9115": BK9115,
        "bk_914x": BK914X,
        "keysight_e36100": KeysightE36100,
        "keysight_n5700": KeysightN5700,
        "rigol_dp800": RigolDP800,
        "siglent_spd3303": SiglentSPD3303,
        "simulated": SimulatedPSU,
        "tdk_lambda_genesys": TDKLambdaGenesys,
    }

    assert set(_registry.keys()) == set(get_args(PSUVendor)), (
        f"_registry and PSUVendor are out of sync: {set(_registry.keys()) ^ set(get_args(PSUVendor))}"
    )

    driver_cls = _registry[config.vendor]

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
