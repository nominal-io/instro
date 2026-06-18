# Protocols

Config-driven protocol clients for direct hardware communication. Unlike instrument drivers
which provide vendor-agnostic abstractions, protocol clients let you talk to any device
that speaks a supported protocol. Just provide a JSON config describing your device's
register map or command set.

| Class | Description |
|-------|-------------|
| [`ModbusDevice`](modbus.md) | Modbus TCP and RTU devices |
| [`EtherNetIPDevice`](ethernetip.md) | Unstable EtherNet/IP tag access |
