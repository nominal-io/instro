# OPC UA

The experimental API is available from `instro.unstable.opcua` in the
`instro-opcua` package. Its [semantic type declarations](https://github.com/nominal-io/instro/blob/main/packages/instro-opcua/instro/unstable/_opcua.pyi)
list the complete signatures and native setting types. The
[OPC UA guide](/library/protocols/opcua.md) covers navigation and ownership.

| API | Behavior |
| --- | --- |
| `OpcUaClient.connect()` | Synchronous connection with native builder settings, anonymous, username/password, or X.509 user authentication. |
| `OpcUaClient.enable_reconnects()` / `disable_reconnects()` | Native recovery control; retry and recreation settings retain native scope and defaults. |
| `Node` | Local `node_id`, relative navigation, complete child enumeration, and typed standard-attribute getters. |
| `DataValue[T]` | Optional payload, native status and metadata, and normalized source/server Unix nanoseconds. |
| `DateTime` | Native timestamp with `ticks()` and integer `unix_timestamp_nanos()`. |
| `Duration` | Native duration with second, millisecond, and nanosecond constructors and conversions. |
| `Subscription[T]` | Blocking iterator of `(Node, DataValue[T])`, explicit `close()`, and context management. |
| `MonitoredItemSettings` | Native per-item sampling, queue, filter, mode, index range, and data encoding. |
| `OpcUaError` | Rust-backed exception with read-only native `status_code` and optional rejected `node`. |

`connect()` accepts native client-builder settings as keywords. Native duration
parameters use `Duration`; numeric protocol intervals preserve native units.
`channel_lifetime` and `session_timeout` use milliseconds. Sampling intervals use
milliseconds, where negative inherits publishing and zero requests fastest
practical sampling. The stubs document the remaining units and special values.

Issued tokens, token renewal callbacks, reverse connections, and custom type
loaders are outside this API's scope.
