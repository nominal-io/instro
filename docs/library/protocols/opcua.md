---
myst:
  html_meta:
    description: "Connect, browse, read attributes, and iterate OPC UA subscriptions from Python"
---

# OPC UA

{.lead}
Browse server Nodes, read typed attributes, and consume subscription samples from Python.

The `instro-opcua` package provides a synchronous client under
`instro.unstable.opcua`. Install it separately:

```bash
pip install instro-opcua
```

## Connect and read

```python
from instro.unstable.opcua import OpcUaClient

with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
    clock = client.objects.get_child("/Server/ServerStatus/CurrentTime")
    sample = clock.value
    print(clock.node_id, sample.value, sample.status.name())
    print(sample.source_unix_timestamp)
```

Connections default to anonymous identity with security policy and mode `None`.
Use `get_endpoints(endpoint_url)` to discover available endpoints before choosing
an identity and security settings. Endpoint descriptions include policy, mode,
server certificate, supported user tokens, and application metadata.

```python
from instro.unstable.opcua import IdentityToken, OpcUaClient, get_endpoints
from instro.unstable.opcua.types import MessageSecurityMode, SecurityPolicy

endpoint = "opc.tcp://localhost:4840"
for description in get_endpoints(endpoint):
    print(description.endpoint_url, description.security_policy, description.security_mode)

with OpcUaClient.connect(
    endpoint,
    identity=IdentityToken.user_name("operator", "password"),
    security_policy=SecurityPolicy.BASIC256_SHA256,
    security_mode=MessageSecurityMode.SIGN_AND_ENCRYPT,
    certificate_path="client.der",
    private_key_path="client.pem",
) as client:
    print(client.objects.children)
```

`IdentityToken.x509()` supplies a **user** certificate and key. The connection's
`certificate_path` and `private_key_path` supply the **application** certificate
and key; provide both together. The [API reference](/ref/protocols/opcua.md)
lists certificate trust controls and all named native connection settings.

Leaving the client context disconnects every Node and subscription derived from
that client. Nodes retain shared client ownership, so deleting the original
client variable alone does not disconnect them. Use contexts or explicit
`disconnect()` and `close()` for synchronous cleanup; finalization only initiates
best-effort cleanup.

## Navigate from RootFolder

`client.get_node(path)` interprets a string or `RelativePath` from RootFolder.
`client.objects`, `client.types`, and `client.views` expose the standard folders.
`node.get_child(path)` starts at that Node and retains the accumulated path from
RootFolder. `node.browse_path` and `node.node_id` are local metadata; they remain
readable after disconnection. Reading an attribute still requires a connection.

`node.children` returns all immediate local children, following browse pagination.
It omits out-of-server references with one aggregated `RuntimeWarning`. A failed
enumeration raises without returning a partial list. Singular path lookup rejects
ambiguous, unresolved, or out-of-server targets with `OpcUaError`.

String paths use the native `async-opcua` parser, which has a known limitation
with multi-digit namespace indexes. Construct a path from qualified names when
needed:

```python
from instro.unstable.opcua.types import QualifiedName, RelativePath

path = RelativePath.from_qualified_names([
    QualifiedName(0, "Objects"),
    QualifiedName(12, "Machine"),
    QualifiedName(12, "Temperature"),
])
# Inside a connected client context:
# temperature = client.get_node(path)
```

## Inspect payloads and quality

Attribute reads return `AttributeValue[T]`. Named getters select semantic types:
`node.node_class.value` is a `NodeClass`, `node.display_name.value` is a
`LocalizedText`, and `node.array_dimensions.value` is a `list[int]`, when present.
`node.get_attribute(AttributeId.NODE_ID)` queries the server's NodeId attribute;
`node.node_id` reads the retained identifier.

Each result preserves `attribute_id`, `status`, and available source/server
timestamps even if the payload is absent or unsupported. Bad or Uncertain quality
does not by itself raise. Check `sample.status.is_good`, `.is_uncertain`, or
`.is_bad` according to the application's quality policy. A missing or empty
payload is `None`.

Payload conversion happens on `.value`. An incompatible fixed attribute payload
raises `OpcUaError` with `BadTypeMismatch`; a valid unsupported payload or a
multidimensional array raises `BadNotSupported`. The original result metadata
remains available. The [value reference](/ref/protocols/opcua.md#supported-values)
lists supported scalars, one-dimensional lists, and structures.

OPC UA DateTime **payloads** become integer Unix nanoseconds. Timestamp **metadata**
uses `DateTime` wrappers; `source_unix_timestamp` and `server_unix_timestamp`
combine the corresponding timestamp and 10-picosecond fractional field, truncating
subnanosecond precision. Source and server metadata remain separate.

## Iterate subscription samples

```python
from instro.unstable.opcua import OpcUaClient

with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
    paths = (path for path in ["/Objects/Server/ServerStatus/CurrentTime"])
    with client.subscribe(paths, publishing_interval=0.5, poll_interval=2.0) as samples:
        for node, sample in samples:
            print(node.node_id, sample.value, sample.status.name())
            break
```

`subscribe()` consumes any iterable of Nodes, strings, or `RelativePath` objects
once and preserves its order. Paths start at RootFolder. Every Node must belong
to the client; empty groups and duplicate resolved NodeIds are rejected. One
attribute is monitored for the entire group, defaulting to `Value`. Setup succeeds
only after all items are established. Item rejections raise an `ExceptionGroup`
with each rejected Node and status in request order, after rollback.

`publishing_interval` and optional `poll_interval` accept numeric seconds,
`datetime.timedelta`, or `Duration`. `sampling_interval` uses native floating
point **milliseconds**, including fractional values and the negative sentinel
for the subscription's publishing interval. Other settings and defaults appear
in the [API reference](/ref/protocols/opcua.md).

Subscription processing continues while Python is idle or slow. With polling
enabled, each tick first emits unmatched pending polls, then reads Nodes with
no processed notification in the current interval. The first tick waits one
period; ready ticks take priority over notifications, and missed ticks are
skipped rather than replayed.

At most one read is in flight and one pending poll is retained per Node. Pending
polls wait for a matching notification, the next tick, or termination. If either
comparable source or server timestamp, including its fractional field, shows an
older poll, that poll precedes the notification; otherwise it is suppressed.
Successful cycles reset quiet membership; failed reads log and preserve it.
Notifications received during a read wait for its completion before processing.
Accepted samples have no wrapper buffer limit or cross-Node timestamp order.

Waiting for the next sample responds cooperatively to Python signals, including
`KeyboardInterrupt`. Interruption exits that wait; iteration can resume without
closing the subscription or discarding queued samples. If the exception leaves
the subscription context, context exit closes it.

`close()` seals callback admission, prevents new polls, waits for an admitted
read, and attempts remote deletion. An already absent session needs no deletion.
Accepted notifications and pending polls remain iterable after close; later
callbacks are excluded. Normal completion drains then ends iteration; a terminal
native error drains then raises its original status. Deletion errors come from
`close()`. If the context body also raises, its exception remains primary and
cleanup failure is secondary context.

## Recovery and shutdown

Native `async-opcua` reconnect settings control recovery, including retry limits,
backoff, keep-alives, and subscription restoration. Temporary transport loss keeps
the shared session; operations during an outage can still fail. Enabling reconnects
does not restart an event loop that has already finished.

Connection waits respond to Python signals. Interrupting setup initiates cleanup
without waiting for it to complete. Explicit `disconnect()` and `close()` release
the GIL while waiting for the initiating caller's cleanup; those waits do not
promise prompt signal handling. Repeated cleanup is harmless and does not add a
shared completion barrier for concurrent callers.

The current `async-opcua` 0.19 backend has a known subscription restoration/deletion
coordination limitation: recreation can change a server subscription ID, and
removal after an awaited deletion response can race restoration. Remote deletion
across recovery therefore has this native limitation. Local stop/join, queued
output, and error precedence still apply. During client recovery, disconnect may
terminate the native event loop without graceful protocol shutdown.
