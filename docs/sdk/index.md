# instro SDK

The `instro` SDK provides a unified Python interface for controlling lab instruments,
collecting measurements, and publishing data to [Nominal](https://nominal.io).

This site is the API reference. For installation, quickstarts, and per-instrument
guides, see the [instro documentation](https://instro.nominal.io).

## Overview

The SDK is organized into several key components:

- **Library**: Base classes and infrastructure for building instrument integrations,
  including the [`Instrument`](library/instrument.md) base class, communication interfaces,
  and data publishers.

- **Instruments**: High-level, vendor-agnostic interfaces for common instrument types:
  [DAQ](instruments/daq.md), [DMM](instruments/dmm.md), [PSU](instruments/psu.md),
  [Electronic Load](instruments/eload.md), [Flow Controller](instruments/flowcontroller.md),
  [Scope](instruments/scope.md), [I2C](instruments/i2c.md), and [AWG](instruments/awg.md).

- **Protocols**: Config-driven clients for direct hardware communication via standard
  wire protocols: [Modbus](protocols/modbus.md) and
  [EtherNet/IP](protocols/ethernetip.md).

- **Drivers**: Vendor-specific implementations that connect instrument interfaces to real hardware.

- **Publishers**: Data publishing backends for exposing measurements to other services.  
  [Nominal Core](library/publishers.md#nominal-core-publisher),
  [Nominal Connect](library/publishers.md#nominal-connect-publisher),
  writing to [files](library/publishers.md#file-publishers), or custom implementations.

## Quick Links

| Section | Description |
|---------|-------------|
| [Library](library/instrument.md) | `Instrument`, `Measurement`, `Command`, and base types |
| [User guides](https://instro.nominal.io) | Installation, quickstarts, and per-instrument guides |
| [Changelog](changelog.md) | Release history and version changes |

```{toctree}
:hidden:
:caption: Overview

Overview <self>
```

```{toctree}
:hidden:
:caption: Instruments

instruments/index
instruments/daq
instruments/dmm
instruments/psu
instruments/eload
instruments/scope
instruments/flowcontroller
instruments/i2c
instruments/awg
```

```{toctree}
:hidden:
:caption: Library

library/instrument
library/types
library/config
library/exceptions
library/publishers
library/discover
library/transports
```

```{toctree}
:hidden:
:caption: Protocols

protocols/index
protocols/modbus
protocols/ethernetip
```

```{toctree}
:hidden:
:caption: Changelog

changelog
```
