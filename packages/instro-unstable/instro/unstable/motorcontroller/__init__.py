"""Motor-controller instrument interface package (unstable)."""

from instro.unstable.motorcontroller.motorcontroller import InstroMotorController, MotorControllerDriverBase
from instro.unstable.motorcontroller.types import (
    BUS_VOLTAGE_KEY,
    DUTY_CYCLE_KEY,
    FET_TEMPERATURE_KEY,
    INPUT_CURRENT_KEY,
    MOTOR_CURRENT_KEY,
    MOTOR_TEMPERATURE_KEY,
    POSITION_KEY,
    VELOCITY_KEY,
    DriveState,
    MotorTelemetry,
)

__all__ = [
    "DriveState",
    "InstroMotorController",
    "MotorControllerDriverBase",
    "MotorTelemetry",
    "BUS_VOLTAGE_KEY",
    "DUTY_CYCLE_KEY",
    "FET_TEMPERATURE_KEY",
    "INPUT_CURRENT_KEY",
    "MOTOR_CURRENT_KEY",
    "MOTOR_TEMPERATURE_KEY",
    "POSITION_KEY",
    "VELOCITY_KEY",
]
