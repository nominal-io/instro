---
orphan: true
card: Keysight 1200X
image: Keysight1200X.png
---

# Keysight1200X

{.lead}
Control the Keysight 1200X oscilloscope from Python through [`InstroScope`](/scope.md).

![Keysight 1200X](Keysight1200X.png){.driver-image}

The {py:obj}`Keysight1200X <instro.scope.drivers.keysight_1200x.Keysight1200X>` provides a driver that can be used to instantiate an [InstroScope](/scope.md).

## Creating an [`InstroScope`](/scope.md) with {py:obj}`Keysight1200X <instro.scope.drivers.keysight_1200x.Keysight1200X>`

```python
from instro import InstroScope
from instro.drivers import Keysight1200X

scope = InstroScope(
    name="myScope",
    driver=Keysight1200X("<visa_resource>"),
    num_channels=4,
)
```

Parameters and methods specific to {py:obj}`Keysight1200X <instro.scope.drivers.keysight_1200x.Keysight1200X>` can be found in the [Reference](/ref/index.md).
