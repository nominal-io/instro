---
orphan: true
card: Keysight E36100
image: KeysightE36100.png
---

# KeysightE36100

{.lead}
Control the Keysight E36100 power supply from Python through [`InstroPSU`](/psu.md).

![Keysight E36100](KeysightE36100.png){.driver-image}

The {py:obj}`KeysightE36100 <instro.psu.drivers.keysight_e36100.KeysightE36100>` provides a driver that can be used to instantiate an [InstroPSU](/psu.md).

## Creating an [`InstroPSU`](/psu.md) with {py:obj}`KeysightE36100 <instro.psu.drivers.keysight_e36100.KeysightE36100>`

```python
from instro.psu.drivers import KeysightE36100
from instro.psu import InstroPSU

psu = InstroPSU(
    name="myPSU",
    driver=KeysightE36100(visa_resource="USB0::..."),
    num_channels=1,
)
```

Parameters and methods specific to {py:obj}`KeysightE36100 <instro.psu.drivers.keysight_e36100.KeysightE36100>` can be found in the [SDK](/sdk/index.md).
