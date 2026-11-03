---
orphan: true
card: Modbus RTU
image: ModbusRTU.png
myst:
  html_meta:
    description: "A connection type for ModbusDevice"
---

# Modbus RTU

{.lead}
A connection type for [`ModbusDevice`](/library/protocols/modbus.md)

![Modbus RTU](ModbusRTU.png){.driver-image}

Modbus RTU is a connection type for [`ModbusDevice`](/library/protocols/modbus.md), not a separate driver class: set `"transport": "rtu"` in the config's connection block, or pass a `ModbusRTUTransport` explicitly. The Python code is identical to Modbus TCP; only the connection block changes.

## Creating a {py:obj}`ModbusDevice <instro.modbus.ModbusDevice>` over RTU

```json
{
  "version": 1,
  "protocol": "modbus",
  "device": {
    "name": "field_sensor",
    "description": "RS-485 field temperature sensor"
  },
  "connection": {
    "transport": "rtu",
    "port": "/dev/ttyUSB0",
    "baudrate": 9600,
    "parity": "N",
    "stopbits": 1,
    "bytesize": 8,
    "timeout": 2.0,
    "unit_id": 1
  },
  "registers": [
    {
      "name": "temperature",
      "starting_address": 0,
      "register_type": "input",
      "data_type": "float32",
      "poll": true
    }
  ]
}
```

```python
from instro.modbus import ModbusDevice

device = ModbusDevice(config="field_sensor.json")
device.open()
```

Parameters and methods specific to {py:obj}`ModbusDevice <instro.modbus.ModbusDevice>` can be found in the [SDK](/sdk/index.md).
