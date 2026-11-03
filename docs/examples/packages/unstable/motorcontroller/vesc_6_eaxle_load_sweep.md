---
myst:
  html_meta:
    description: "instro Python example: E-axle load sweep, one VESC 6 under test driven against two VESC 6 load motors on a shared CAN bus. Stand: the motor under test (DUT) drives a reduction to an axle, and a load motor on each axle output brakes it. All three VESCs hang off one gs_usb adapter, so the three drivers share one CanTransport. The DUT holds a velocity setpoint while both load motors step through brake-current levels; brake current opposes each motor's own rotation, so the two loads cannot fight through the differential."
---

# E-axle load sweep, one VESC 6 under test driven against two VESC 6 load motors on a shared CAN bus

:::{warning}
This example uses `instro-unstable`. This code is new and may change without notice.
:::

```{literalinclude} /../packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6_eaxle_load_sweep.py
:caption: vesc_6_eaxle_load_sweep.py
:language: python
```

Source: [`packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6_eaxle_load_sweep.py`](https://github.com/nominal-io/instro/blob/main/packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6_eaxle_load_sweep.py)
