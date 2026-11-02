"""I2C drivers package. Vendor drivers ship in vendor packages (e.g. ``instro-i2c-aardvark``); ``VirtualI2C`` is built in."""

from pkgutil import extend_path

# Let workspace packages contribute concrete vendor subpackages under
# instro.i2c.drivers.* (e.g. instro.i2c.drivers.totalphase).
__path__ = extend_path(__path__, __name__)

from instro.i2c import I2CDriverBase
from instro.i2c.drivers.virtual import VirtualI2C

__all__ = ["I2CDriverBase", "VirtualI2C"]
