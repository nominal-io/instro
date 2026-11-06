"""I2C instrument interface package."""

from pkgutil import extend_path

# Let workspace packages (e.g. instro-i2c-aardvark) contribute subpackages
# under instro.i2c.*.
__path__ = extend_path(__path__, __name__)

from instro.i2c.i2c import I2CDriverBase, I2CInterface
from instro.i2c.types import (
    CommandDef,
    CommandDevice,
    CustomScaling,
    DataFormat,
    FieldDef,
    LinearScaling,
    RegisterDef,
    RegisterDevice,
    ScalingFunction,
    SystemDefinition,
)

__all__ = [
    "I2CDriverBase",
    "I2CInterface",
    "SystemDefinition",
    "RegisterDevice",
    "CommandDevice",
    "ScalingFunction",
    "LinearScaling",
    "CustomScaling",
    "DataFormat",
    "CommandDef",
    "FieldDef",
    "RegisterDef",
]
