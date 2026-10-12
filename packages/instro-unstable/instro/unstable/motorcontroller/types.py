"""Motor-controller shared types."""

import sys
from typing import TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    from typing_extensions import NotRequired


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
