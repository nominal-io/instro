# Instruments

The `instro` SDK provides high-level, vendor-agnostic interfaces for common lab
instruments. Each instrument type defines a standard API that works across multiple hardware vendors.

| Class | Description |
|-------|-------------|
| [`InstroDAQ`](daq.md) | Data acquisition systems |
| [`InstroDMM`](dmm.md) | Digital multimeters |
| [`InstroPSU`](psu.md) | Programmable power supply units |
| [`InstroELoad`](eload.md) | Electronic loads |
| [`I2CInterface`](i2c.md) | I2C bus communication devices |

Each instrument page includes the interface, configuration types, driver base classes,
and vendor-specific driver implementations.
