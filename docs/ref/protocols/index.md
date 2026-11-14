# Protocols

Protocol clients communicate directly with hardware. Modbus and EtherNet/IP clients use
JSON config files to describe register maps or tags. The OPC UA client exposes direct
connection, browsing, attribute reads, and subscription primitives.

| Class | Description |
|-------|-------------|
| [`ModbusDevice`](modbus.md) | Modbus TCP and RTU devices |
| [`EtherNetIPDevice`](ethernetip.md) | Allen-Bradley PLC tag access |
| [`OpcUaClient`](opcua.md) | OPC UA browsing, attributes, and subscriptions |
