---
myst:
  html_meta:
    description: "instro Python example: Single VESC 6 (VESC ID 0) on a CAN bus via a candleLight/gs_usb USB-CAN adapter. Wire CAN_H/CAN_L (and GND) from the adapter to the VESC's COMM port; 120 ohm termination at both ends of the bus. Other VESCs may share the bus; frames are filtered to CONTROLLER_ID."
---

# Single VESC 6 (VESC ID 0) on a CAN bus via a candleLight/gs_usb USB-CAN adapter

:::{warning}
This example uses `instro-unstable`. This code is new and may change without notice.
:::

```{literalinclude} /../packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6_single_motor.py
:caption: vesc_6_single_motor.py
:language: python
```

Source: [`packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6_single_motor.py`](https://github.com/nominal-io/instro/blob/main/packages/instro-unstable/instro/unstable/motorcontroller/examples/vesc_6_single_motor.py)
