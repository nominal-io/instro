from enum import Enum


class SweepType(Enum):
    """the kind of frequency sweep."""

    LIN = "LIN"
    LOG = "LOG"


class NetworkFileFormat(Enum):
    SNP = "SNP"
