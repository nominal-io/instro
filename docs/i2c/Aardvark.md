---
orphan: true
card: Total Phase Aardvark
image: Aardvark.png
---

# Aardvark

{.lead}
Control the Total Phase Aardvark I2C adapter from Python through [`I2CInterface`](/library/protocols/i2c/overview.md).

![Total Phase Aardvark](Aardvark.png){.driver-image}

The {py:obj}`Aardvark <instro.i2c.drivers.totalphase.aardvark.Aardvark>` provides a driver that can be used to instantiate an [I2CInterface](/library/protocols/i2c/overview.md).

## Creating an [`I2CInterface`](/library/protocols/i2c/overview.md) with {py:obj}`Aardvark <instro.i2c.drivers.totalphase.aardvark.Aardvark>`

```python
from instro.i2c.drivers.totalphase import Aardvark
from instro.i2c import I2CInterface
from instro.i2c.types import SystemDefinition

# Create system definition (see the System Definition page for details)
system = SystemDefinition()
# ... add devices to system definition ...

i2c = I2CInterface(
    name="main_i2c",
    driver=Aardvark(serial_number="123456"),
    system_definition=system,
)
```

Parameters and methods specific to {py:obj}`Aardvark <instro.i2c.drivers.totalphase.aardvark.Aardvark>` can be found in the [SDK](/sdk/index.md).
