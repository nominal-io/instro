"""DAQ scaling package."""

from instro.daq.scaling.scaling import DevicePassthrough, LinearScaler, ReverseLinearScaler, Scaler, ScalerPipeline
from instro.daq.scaling.thermocouple import (
    TC_TYPE,
    TC_UNIT,
    InverseThermocoupleSensor,
    ThermocoupleSensor,
    kelvin_to_unit,
    unit_to_kelvin,
)

__all__ = [
    "DevicePassthrough",
    "LinearScaler",
    "ScalerPipeline",
    "ReverseLinearScaler",
    "Scaler",
    "TC_TYPE",
    "TC_UNIT",
    "InverseThermocoupleSensor",
    "ThermocoupleSensor",
    "kelvin_to_unit",
    "unit_to_kelvin",
]
