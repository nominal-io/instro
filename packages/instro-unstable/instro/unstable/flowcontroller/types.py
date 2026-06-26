"""Flow-controller shared types."""

import sys
from typing import Final, Literal, TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    from typing_extensions import NotRequired

# Constants matching FlowData's field names.
# Typed Final[Literal[...]] so they work as TypedDict subscript keys.
# Driver-specific keys (e.g. AlicatMC.GAS_KEY) follow the same pattern.
SETPOINT_KEY: Final[Literal["setpoint"]] = "setpoint"
MASS_FLOW_KEY: Final[Literal["mass_flow"]] = "mass_flow"
VOLUMETRIC_FLOW_KEY: Final[Literal["vol_flow"]] = "vol_flow"
PRESSURE_KEY: Final[Literal["pressure"]] = "pressure"
TEMPERATURE_KEY: Final[Literal["temperature"]] = "temperature"


class FlowData(TypedDict):
    """Standard flow-controller measurement frame."""

    setpoint: float
    mass_flow: float
    vol_flow: float
    # not required
    pressure: NotRequired[float]
    temperature: NotRequired[float]
