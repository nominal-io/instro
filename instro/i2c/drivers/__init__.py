"""I2C drivers package. Concrete drivers ship in vendor packages (e.g. ``instro-i2c-aardvark``)."""

from pkgutil import extend_path

# Let workspace packages contribute concrete vendor subpackages under
# instro.i2c.drivers.* (e.g. instro.i2c.drivers.totalphase).
__path__ = extend_path(__path__, __name__)

from instro.i2c import I2CDriverBase

__all__ = ["I2CDriverBase"]
