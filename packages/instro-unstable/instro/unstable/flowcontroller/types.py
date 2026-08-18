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
    """Base flow-controller measurement frame. All controllers measure setpoint and pressure."""

    setpoint: float
    pressure: float


class MassFlowData(FlowData):
    """Mass-flow controller measurement frame (e.g. Alicat MC-series). Adds flow and temperature measurements."""

    mass_flow: float
    vol_flow: float
    temperature: NotRequired[float]


class LiquidFlowData(FlowData):
    """Liquid-flow controller measurement frame. Adds volumetric flow (but not mass flow)."""

    vol_flow: float
    temperature: NotRequired[float]


class PressureData(FlowData):
    """Gauge-pressure controller measurement frame. Only setpoint and pressure (from base)."""

    pass
