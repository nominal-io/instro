---
myst:
  html_meta:
    description: "Synchronous OPC UA navigation, attribute reads, and subscriptions"
---

# OPC UA

The experimental `instro-opcua` package provides a synchronous Python API over
`async-opcua`. Protocol operations, connection recovery, and native data types run
in the Rust extension.

```python
from instro.unstable.opcua import AttributeId, Duration, OpcUaClient

with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
    temperature = client.objects.get_child("/2:Temperature")
    sample = temperature.value
    print(temperature.node_id, sample.value, sample.source_unix_timestamp)

    with client.subscribe(
        [temperature],
        attribute=AttributeId.VALUE,
        publishing_interval=Duration.from_millis(500),
    ) as subscription:
        source, sample = next(subscription)
        print(source.node_id, sample.value, sample.status)
```

## Navigation and reads

`objects`, `types`, and `views` return address-space entry Nodes. Use `get_child()`
to resolve a relative path and `children` to enumerate all immediate children,
including continuation pages. `get_node(NodeId(...))` creates a local Node handle;
`get_node(path)` resolves a path from the Root Node.

Relative-path strings use upstream's qualified-name syntax and escaping. The
upstream 0.19.0 string parser misparses multi-digit target namespace indexes. Use
the native structured form for those indexes:

```python
from instro.unstable.opcua import QualifiedName, RelativePath

path = RelativePath.from_qualified_names([QualifiedName(12, "Machine")])
```

`node.node_id` is local and performs no server read. Other standard attribute
getters return a `DataValue`, including when its payload is absent or its status
is Bad or Uncertain. Inspect `sample.status` to assess quality. Access-denial
sample statuses follow the same rule. Failed operations raise `OpcUaError` with
the unchanged native status code.

## Values and timestamps

Known attributes have typed payloads, such as `LocalizedText` for `display_name`
and `NodeClass` for `node_class`. Variable values support native scalars, arrays,
matrices, and structured values. `Array` preserves upstream element-type and
dimension information, and unknown `ExtensionObject` values expose their type
identifier, encoding, and encoded body.

`DateTime` stays a native wrapped value. `unix_timestamp_nanos()` returns integer
nanoseconds since the Unix epoch. A `DataValue` exposes the native source and
server timestamps and picosecond fields separately. Its `source_unix_timestamp`
and `server_unix_timestamp` getters combine each timestamp with its corresponding
fraction, truncating subnanosecond precision. Missing timestamps remain `None`.

## Subscription ownership

Each group contains unique NodeIds and selects one standard attribute shared by
its Nodes. Per-item native monitoring settings remain available. Setup succeeds
for every item or raises an `ExceptionGroup` containing an `OpcUaError` for each
rejected Node, in request order. Failed setup attempts subscription deletion
before reporting the original failure.

Iteration blocks until output or termination and permits Python interrupts. The
local delivery queue is unbounded. Native completion drains that queue before
`StopIteration`; native failure drains it before raising `OpcUaError`.

`close()` and context exit stop production, flush pending polls, preserve queued
output, and wait for native subscription deletion. A deletion failure raises
from the closing operation. If the context body also raised, that original
exception remains primary and the cleanup error remains in its exception context.

Optional `poll_interval=Duration.from_secs(5)` enables background reads of Nodes
without notifications during the interval. Poll results reconcile with matching
notifications before entering the queue. When either comparable source or server
timestamp shows an older poll, it is delivered before the notification; otherwise
the matching poll is suppressed. Unmatched polls flush at the next tick or at
termination. No ordering is promised between different Nodes.

See the [API reference](/ref/protocols/opcua.md) for signatures and configuration.
