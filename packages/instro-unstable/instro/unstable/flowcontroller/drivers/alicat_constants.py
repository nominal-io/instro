"""Alicat device constants and mappings."""

from enum import IntEnum

from instro.unstable.flowcontroller.types import MASS_FLOW_KEY, PRESSURE_KEY, VOLUMETRIC_FLOW_KEY


class LoopVariable(IntEnum):
    """Loop control variable codes (from Alicat Serial Primer)."""

    MASS_FLOW = 37
    VOLUMETRIC_FLOW = 36
    ABSOLUTE_PRESSURE = 34
    GAUGE_PRESSURE = 38
    PRESSURE_DIFFERENTIAL = 39


# Legacy module-level constants for backwards compatibility
LOOP_VARIABLE_ABS_PRESSURE = LoopVariable.ABSOLUTE_PRESSURE
LOOP_VARIABLE_PRESSURE_DIFF = LoopVariable.PRESSURE_DIFFERENTIAL
LOOP_VARIABLE_GAUGE_PRESSURE = LoopVariable.GAUGE_PRESSURE
LOOP_VARIABLE_MASS_FLOW = LoopVariable.MASS_FLOW
LOOP_VARIABLE_VOL_FLOW = LoopVariable.VOLUMETRIC_FLOW

_LOOP_VAR_TO_KEY = {
    LOOP_VARIABLE_MASS_FLOW: MASS_FLOW_KEY,
    LOOP_VARIABLE_VOL_FLOW: VOLUMETRIC_FLOW_KEY,
    LOOP_VARIABLE_ABS_PRESSURE: PRESSURE_KEY,
    LOOP_VARIABLE_GAUGE_PRESSURE: PRESSURE_KEY,
    LOOP_VARIABLE_PRESSURE_DIFF: "pressure_diff",
}
