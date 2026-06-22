from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from instro.lib.publishers import Publisher
    from instro.psu.psu import InstroPSU

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

# now we make our little class for the PSU config


class PSUConfig(BaseModel):
    """Validated config for constructing an InstroPSU from JSON."""

    model_config = ConfigDict(extra="allow")
    name: str = Field(description="Channel-name prefix for published data.")
    vendor: PSUVendor = Field(description="PSU vendor/model key.")
    connection: str = Field(description="VISA resource string (e.g 'USB0::...' or 'TCPIP0::...').")
    num_channels: int = Field(ge=1, description="Number of output channels.")
    visa_backend: str = Field(default="@py", description="pyvisa backend specifier.")


def build_psu_from_config(
    config: PSUConfig,
    publishers: list[Publisher] | None = None,
    **kwargs: Any,
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

    _registry: dict[str, type] = {
        "bk_9115": BK9115,
        "bk_914x": BK914X,
        "keysight_e36100": KeysightE36100,
        "keysight_n5700": KeysightN5700,
        "rigol_dp800": RigolDP800,
        "siglent_spd3303": SiglentSPD3303,
        "simulated": SimulatedPSU,
        "tdk_lambda_genesys": TDKLambdaGenesys,
    }

    driver_cls = _registry[config.vendor]
    visa_config = VisaConfig(visa_resource=config.connection, visa_backend=config.visa_backend)
    driver = driver_cls(visa_config)

    merged_kwargs = {**(config.model_extra or {}), **kwargs}
    return InstroPSU(
        name=config.name,
        driver=driver,
        num_channels=config.num_channels,
        publishers=publishers,
        **merged_kwargs,
    )
