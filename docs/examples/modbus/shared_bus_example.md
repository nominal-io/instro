---
myst:
  html_meta:
    description: "instro Python example: Two ModbusDevices sharing one Modbus TCP connection. Demonstrates the shared-bus pattern: one ModbusTCPTransport passed to each device, with each device's own unit_id. The transport opens once, for whichever device opens first, and stays open until the last device closes it -- the way one RS-485 multi-drop line (or a TCP-to-serial gateway fronting one) serves several unit addresses over a single connection."
---

# Two ModbusDevices sharing one Modbus TCP connection

```{literalinclude} /../examples/modbus/shared_bus_example.py
:caption: shared_bus_example.py
:language: python
```

Source: [`examples/modbus/shared_bus_example.py`](https://github.com/nominal-io/instro/blob/main/examples/modbus/shared_bus_example.py)
