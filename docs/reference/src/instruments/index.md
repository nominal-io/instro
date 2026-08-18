# Instruments

The `instro` SDK provides high-level, vendor-agnostic interfaces for common lab
instruments. Each instrument type defines a standard API that works across multiple hardware vendors.

| Class | Description |
|-------|-------------|
| [`InstroDAQ`](daq.md) | Data acquisition systems |
| [`InstroDMM`](dmm.md) | Digital multimeters |
| [`InstroPSU`](psu.md) | Programmable power supply units |
| [`InstroELoad`](eload.md) | Electronic loads |
| [`InstroScope`](scope.md) | Oscilloscopes |
| [`InstroFlowController`](flowcontroller.md) | Flow Controllers |
| [`I2CInterface`](i2c.md) | I2C bus communication devices |
| [`InstroAWG`](awg.md) | Arbitrary waveform generators (unstable) |

Each instrument page includes the interface, configuration types, driver base classes,
and vendor-specific driver implementations. Errors raised by instrument methods are
documented in [Exceptions](../reference/exceptions.md).

## Supported devices

The table below is included verbatim from the repository README, so the two can't drift.

--8<-- "README.md:supported-devices"

The Modbus and EtherNet/IP rows are covered under [Protocols](../protocols/index.md).
Community-contributed drivers are documented in the
[instro-contrib guide](https://instro.nominal.io/instrumentation/contrib) rather than this reference.
