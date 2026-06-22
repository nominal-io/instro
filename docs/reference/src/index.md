# instro SDK

The `instro` SDK provides a unified Python interface for controlling lab instruments,
collecting measurements, and publishing data to [Nominal](https://nominal.io).

## Overview

The SDK is organized into several key components:

- **Library**: Base classes and infrastructure for building instrument integrations,
  including the [`Instrument`](reference/instrument.md) base class, communication interfaces,
  and data publishers.

- **Instruments**: High-level, vendor-agnostic interfaces for common instrument types:
  [DAQ](instruments/daq.md), [DMM](instruments/dmm.md), [PSU](instruments/psu.md),
  [Electronic Load](instruments/eload.md), and [I2C](instruments/i2c.md).

- **Protocols**: Config-driven clients for direct hardware communication via standard
  wire protocols: [Modbus](protocols/modbus.md) and unstable
  [EtherNet/IP](protocols/ethernetip.md).

- **Drivers**: Vendor-specific implementations that connect instrument interfaces to real hardware.

- **Publishers**: Data publishing backends for exposing measurements to other services.  
  [Nominal Core](reference/publishers.md#nominal-core-publisher),
  [Nominal Connect](reference/publishers.md#nominal-connect-publisher),
  writing to [files](reference/publishers.md#file-publishers), or custom implementations.

## Quick Links

| Section | Description |
|---------|-------------|
| [Library](reference/instrument.md) | `Instrument`, `Measurement`, `Command`, and base types |
| [Changelog](changelog.md) | Release history and version changes |
