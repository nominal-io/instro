"""E-Load shared types: ``LoadMode`` and ``SlewRateDirection``."""

from enum import Enum


class LoadMode(Enum):
    CC = "CC"
    CR = "CR"
    CP = "CP"
    CV = "CV"


class SlewRateDirection(Enum):
    RISE = "RISE"
    FALL = "FALL"
    BOTH = "BOTH"
