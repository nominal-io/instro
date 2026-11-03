---
myst:
  html_meta:
    description: "instro Python example: VESC 6 over CAN with a Canable 2.0 Pro, behind InstroMotorController. Spins the motor with a current setpoint while the background daemon drains the VESC's broadcast status frames into the channel buffer (and any publishers)."
---

# VESC 6 over CAN with a Canable 2.0 Pro, behind InstroMotorController

:::{warning}
This example uses `instro-unstable`. This code is new and may change without notice.
:::

```{literalinclude} /../packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6.py
:caption: vesc_6.py
:language: python
```

Source: [`packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6.py`](https://github.com/nominal-io/instro/blob/main/packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6.py)
