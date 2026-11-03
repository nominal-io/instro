---
orphan: true
card: Modbus TCP
image: ModbusTCP.png
myst:
  html_meta:
    description: "A connection type for ModbusDevice"
---

# Modbus TCP

{.lead}
A connection type for [`ModbusDevice`](/library/protocols/modbus.md)

![Modbus TCP](ModbusTCP.png){.driver-image}

Modbus TCP is a connection type for [`ModbusDevice`](/library/protocols/modbus.md), not a separate driver class: set `"transport": "tcp"` in the config's connection block, or pass a `ModbusTCPTransport` explicitly.

## Creating a {py:obj}`ModbusDevice <instro.modbus.ModbusDevice>` over TCP

```python
from instro.modbus import ModbusDevice

# connection block declared inline, e.g. { "transport": "tcp", "host": "192.168.1.100", "port": 502 }
device = ModbusDevice(config="my_device.json")
device.open()
```

Or with an explicit, shareable transport:

```python
from instro.lib.transports import ModbusTCPTransport
from instro.modbus import ModbusDevice

transport = ModbusTCPTransport(host="192.168.1.100", port=502)
device = ModbusDevice(config="my_device.json", connection=transport, unit_id=1)
```

Parameters and methods specific to {py:obj}`ModbusDevice <instro.modbus.ModbusDevice>` can be found in the [SDK](/sdk/index.md).
