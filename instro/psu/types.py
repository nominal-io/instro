"""PSU shared types."""

from enum import Enum


class OperatingMode(Enum):
    """PSU regulation state: which quantity is currently being held constant, unregulated, or off."""

    CONSTANT_VOLTAGE = "CV"
    CONSTANT_CURRENT = "CC"
    UNREGULATED = "UR"
    OFF = "OFF"
