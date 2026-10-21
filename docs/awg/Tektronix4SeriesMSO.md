---
orphan: true
card: Tektronix 4-series MSO AFG
image: Tektronix4SeriesMSO.png
myst:
  html_meta:
    description: "A driver for InstroAWG"
---

# Tektronix4SeriesMSOAFG

{.lead}
A driver for [`InstroAWG`](/awg.md)

![Tektronix 4-series MSO AFG](Tektronix4SeriesMSO.png){.driver-image}

The {py:obj}`Tektronix4SeriesMSOAFG <instro.scope.drivers.tektronix_4series.Tektronix4SeriesMSOAFG>` provides a driver that can be used to instantiate an [InstroAWG](/awg.md).

## Creating an [`InstroAWG`](/awg.md) with {py:obj}`Tektronix4SeriesMSOAFG <instro.scope.drivers.tektronix_4series.Tektronix4SeriesMSOAFG>`

The AFG is the MSO4's built-in function generator (option AFG). It shares the oscilloscope's VISA connection, so both views come from one {py:obj}`Tektronix4SeriesMSO <instro.scope.drivers.tektronix_4series.Tektronix4SeriesMSO>`:

```python
from instro.awg import InstroAWG
from instro.scope import InstroScope
from instro.scope.drivers import Tektronix4SeriesMSO

device = Tektronix4SeriesMSO("<visa_resource>")
scope = InstroScope(name="myScope", driver=device.scope, num_channels=4)
awg = InstroAWG(name="myAFG", driver=device.awg, num_channels=1)
```

The AFG has a single output (channel 1) and programs amplitude in `VPP` only. Sine, square, sawtooth, triangle, pulse, and DC waveforms are supported; phase offsets and pulse delay are not, and arbitrary waveforms are not yet implemented.

Parameters and methods specific to {py:obj}`Tektronix4SeriesMSOAFG <instro.scope.drivers.tektronix_4series.Tektronix4SeriesMSOAFG>` can be found in the [SDK](/sdk/index.md).
