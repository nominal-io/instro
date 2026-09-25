# Transports

Transport drivers own I/O, locking, and connection lifecycle. Concrete instrument drivers compose a
transport in their constructor rather than extending it.

## TransportBase

`TransportBase` is the base every transport implements: `_open_session`, `_teardown_session`, and `is_open`
are the required contract, and the base itself provides the `open`/`close` lifecycle with shared
ownership, so more than one driver can share a single connection. The first `open(holder)` opens it,
and it stays open until the last `close(holder)` frees it. See [Transports](https://instro.nominal.io/library/transports/overview)
in the guides for the lifecycle contract, a worked combined-instrument example, and a walkthrough for
implementing a new transport.

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.lib.transports.transport_base.TransportBase
```

## VisaDriver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.lib.transports.visa.VisaDriver
   ~instro.lib.transports.visa.VisaConfig
   ~instro.lib.transports.visa.SerialConfig
   ~instro.lib.transports.visa.TerminatorConfig
   ~instro.lib.transports.visa.TimeoutConfig
   ~instro.lib.transports.visa.StopBits
   ~instro.lib.transports.visa.Parity
   ~instro.lib.transports.visa.ControlFlow
```

## Modbus transport

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.lib.transports.modbus.ModbusTransport
   ~instro.lib.transports.modbus.ModbusTCPTransport
   ~instro.lib.transports.modbus.ModbusRTUTransport
   ~instro.lib.transports.modbus.register_count
   ~instro.lib.transports.modbus.decode_registers
   ~instro.lib.transports.modbus.encode_value
```
