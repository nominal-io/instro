---
orphan: true
card: Rigol DP800
image: RigolDP800.png
myst:
  html_meta:
    description: "A driver for InstroPSU"
---

# RigolDP800

{.lead}
A driver for [`InstroPSU`](/psu.md)

![Rigol DP800](RigolDP800.png){.driver-image}

The {py:obj}`RigolDP800 <instro.psu.drivers.rigol_dp800.RigolDP800>` provides a driver that can be used to instantiate an [InstroPSU](/psu.md).

## Creating an [`InstroPSU`](/psu.md) with {py:obj}`RigolDP800 <instro.psu.drivers.rigol_dp800.RigolDP800>`

```python
from instro.psu.drivers import RigolDP800
from instro.psu import InstroPSU

psu = InstroPSU(
    name="myPSU",
    driver=RigolDP800(visa_resource="USB0::..."),
    num_channels=3,  # DP811 has 1, DP821 has 2, DP831/DP832 have 3
)
```

## Operating mode

`get_operating_mode` checks the output state first and returns `OperatingMode.OFF` for a disabled channel. For an enabled channel it reads `:OUTP:MODE?`, which reports `CONSTANT_VOLTAGE`, `CONSTANT_CURRENT`, or `UNREGULATED` (the DP800's critical state between CV and CC).

Parameters and methods specific to {py:obj}`RigolDP800 <instro.psu.drivers.rigol_dp800.RigolDP800>` can be found in the [SDK](/sdk/index.md).
