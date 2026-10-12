---
orphan: true
card: DewesoftX
image: DewesoftX.png
myst:
  html_meta:
    description: "A driver for InstroDAQ"
---

# DewesoftX

{.lead}
A driver for [`InstroDAQ`](/daq.md)

![DewesoftX](DewesoftX.png){.driver-image}

:::{warning}
This driver is Windows only and ships in the `instro-unstable` package, whose API may change without notice.
:::

The {py:obj}`DewesoftX <instro.unstable.daq.drivers.dewesoftx.DewesoftX>` driver provides a driver that can be used to instantiate an [InstroDAQ](/daq.md). It attaches over DCOM to a DewesoftX instance already running on the same PC and exposes its live channels.

## Creating an [`InstroDAQ`](/daq.md) with {py:obj}`DewesoftX <instro.unstable.daq.drivers.dewesoftx.DewesoftX>`

```python
from instro.daq import InstroDAQ
from instro.unstable.daq.drivers import DewesoftX

# attaches to the DewesoftX instance running on this Windows PC
daq = InstroDAQ(name="dewesoftDAQ", driver=DewesoftX())
```

Pass `dxd_name` to control the `.dxd` file `start()` stores to; the default names it after the run time.

Parameters and methods specific to {py:obj}`DewesoftX <instro.unstable.daq.drivers.dewesoftx.DewesoftX>` can be found in the [SDK](/sdk/index.md).
