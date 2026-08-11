"""Motor-controller shared types."""

import enum
import sys
from typing import Final, Literal, TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    from typing_extensions import NotRequired

# Constants matching MotorTelemetry's field names.
# Typed Final[Literal[...]] so they work as TypedDict subscript keys.
# Driver-specific keys follow the same pattern.
POSITION_KEY: Final[Literal["position"]] = "position"
VELOCITY_KEY: Final[Literal["velocity"]] = "velocity"
MOTOR_CURRENT_KEY: Final[Literal["motor_current"]] = "motor_current"
INPUT_CURRENT_KEY: Final[Literal["input_current"]] = "input_current"
DUTY_CYCLE_KEY: Final[Literal["duty_cycle"]] = "duty_cycle"
BUS_VOLTAGE_KEY: Final[Literal["bus_voltage"]] = "bus_voltage"
FET_TEMPERATURE_KEY: Final[Literal["fet_temperature"]] = "fet_temperature"
MOTOR_TEMPERATURE_KEY: Final[Literal["motor_temperature"]] = "motor_temperature"


class DriveState(enum.Enum):
    """Coarse drive state; synthesized on devices without a real state machine."""

    DISABLED = "disabled"
    ENABLED = "enabled"
    FAULT = "fault"


class MotorTelemetry(TypedDict):
    """Motor-controller telemetry snapshot. Position in degrees, velocity in mechanical RPM, currents in amps."""

    position: NotRequired[float]
    velocity: NotRequired[float]
    motor_current: NotRequired[float]
    input_current: NotRequired[float]
    duty_cycle: NotRequired[float]
    bus_voltage: NotRequired[float]
    fet_temperature: NotRequired[float]
    motor_temperature: NotRequired[float]
