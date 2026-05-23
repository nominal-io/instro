"""DAQ drivers package. Vendor packages (LabJack, MCC, NI) contribute drivers via workspace subpackages."""

from pkgutil import extend_path

# Let workspace packages contribute concrete vendor subpackages under
# instro.daq.drivers.* (e.g. instro.daq.drivers.labjack).
__path__ = extend_path(__path__, __name__)

from instro.daq import DAQDriverBase, HWTimestamper
from instro.daq.drivers.keysight_34980a import Keysight34980A

__all__ = ["DAQDriverBase", "HWTimestamper", "Keysight34980A"]
