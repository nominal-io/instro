---
myst:
  html_meta:
    description: "Runnable examples from the instro-unstable package."
---

# Unstable

:::{warning}
These examples use `instro-unstable`. This code is new and may change without notice.
:::

## Flow Controller

- [Alicat MC low-level control](flowcontroller/alicat_mc.md)
- [InstroFlowController w/ Alicat MC](flowcontroller/flowcontroller.md)

## Motor Controller

- [VESC 6 over CAN with a Canable 2.0 Pro, behind InstroMotorController](motorcontroller/vesc_6.md)
- [E-axle load sweep, one VESC 6 under test driven against two VESC 6 load motors on a shared CAN bus](motorcontroller/vesc_6_eaxle_load_sweep.md)
- [Single VESC 6 (VESC ID 0) on a CAN bus via a candleLight/gs_usb USB-CAN adapter](motorcontroller/vesc_6_single_motor.md)

## Vna

- [Sweep a NanoVNA, read networks, and save a touchstone file](vna/vna_nanovna_read_touchstone.md)

```{toctree}
:hidden:

flowcontroller/alicat_mc
flowcontroller/flowcontroller
motorcontroller/vesc_6
motorcontroller/vesc_6_eaxle_load_sweep
motorcontroller/vesc_6_single_motor
vna/vna_nanovna_read_touchstone
```
