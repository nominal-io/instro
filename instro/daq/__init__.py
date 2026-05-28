"""Data-acquisition (DAQ) instrument interface package."""

from pkgutil import extend_path

# Let workspace packages (e.g. instro-daq-labjack) contribute subpackages
# under instro.daq.* — without this, the regular-package __init__.py would
# pin `instro.daq` to the main repo's path and hide the workspace trees.
__path__ = extend_path(__path__, __name__)

from instro.daq.daq import (
    DAQDriverBase,
    HWTimestamper,
    HWTimingException,
    InstroDAQ,
)
from instro.daq.types import ChannelType, DAQVendor, RelayChannel

__all__ = [
    "InstroDAQ",
    "DAQDriverBase",
    "HWTimestamper",
    "ChannelType",
    "DAQVendor",
    "HWTimingException",
    "RelayChannel",
]
