# Transports

Transport drivers own I/O, locking, and connection lifecycle. Concrete instrument drivers compose a
transport in their constructor rather than extending it.

## TransportBase

`TransportBase` is the base every transport implements: `_open_session`, `_teardown_session`, and `is_open`
are the required contract, and the base itself provides the `open`/`close` lifecycle with shared
ownership, so more than one driver can share a single connection. The first `open(holder)` opens it,
and it stays open until the last `close(holder)` frees it. See [Transports](https://instro.nominal.io/instrumentation/transports/overview)
in the guides for the lifecycle contract, a worked combined-instrument example, and a walkthrough for
implementing a new transport.

::: instro.lib.transports.transport_base

## VisaDriver

::: instro.lib.transports.visa

## ModbusDriver

::: instro.lib.transports.modbus
