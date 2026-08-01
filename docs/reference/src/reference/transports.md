# Transports

Transport drivers own I/O, locking, and connection lifecycle. Concrete instrument drivers compose a
transport in their constructor rather than extending it.

## Shared Ownership

`OwnershipContext` is the base both transports below inherit. It lets more than one driver share a
single connection: the first `acquire()` opens it, and it stays open until the last `release()`
frees it. See [Shared ownership](https://instro.nominal.io/instrumentation/transports/visa#shared-ownership)
in the guides for a worked combined-instrument example.

::: instro.lib.transports.ownership

## VisaDriver

::: instro.lib.transports.visa

## ModbusDriver

::: instro.lib.transports.modbus
