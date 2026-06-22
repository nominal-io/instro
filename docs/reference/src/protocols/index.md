# Protocols

Config-driven protocol clients communicate directly with hardware. Instrument drivers provide
vendor-agnostic abstractions; protocol clients use JSON config files that describe a device
register map or command set.

| Class | Description |
|-------|-------------|
| [`ModbusDevice`](modbus.md) | Modbus TCP and RTU devices |
| [`EtherNetIPDevice`](ethernetip.md) | Unstable Allen-Bradley PLC tag access |
