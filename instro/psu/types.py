"""PSU shared types."""

from enum import Enum


class OperatingMode(Enum):
    """PSU regulation state: which quantity is currently being held constant, unregulated, or off.

    ``UNREGULATED`` is the critical state between constant voltage and constant current, where neither
    is being held. Only supplies that report it (such as the Rigol DP800 series) return it.
    """

    CONSTANT_VOLTAGE = "CV"
    CONSTANT_CURRENT = "CC"
    UNREGULATED = "UR"
    OFF = "OFF"
