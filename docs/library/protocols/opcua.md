---
orphan: true
myst:
  html_meta:
    description: "Read and write OPC UA node attributes and subscribe to changes from Python"
---

# OPC UA

{.lead}
Use the synchronous OPC UA client to browse nodes, read and write attributes, and subscribe to changes.

Install the package with pip.

```bash
pip install instro-opcua
```

## Connect and read

Read the server clock and check the status returned with the sample.

```python
from instro.unstable.opcua import OpcUaClient

with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
    clock = client.objects.get_child("/Server/ServerStatus/CurrentTime")
    sample = clock.value
    if sample.status.is_good:
        print(clock.node_id, sample.value)
    else:
        print(sample.status.name())
```

Connections default to anonymous identity with security policy and mode `None`.
Use `get_endpoints(endpoint_url)` to discover available endpoints before choosing
an identity and security settings. Endpoint descriptions include policy, mode,
server certificate, supported user tokens, and application metadata.

For this username example, set `OPCUA_PASSWORD` and supply your application's
certificate and private key.

```python
import os

from instro.unstable.opcua import IdentityToken, OpcUaClient, get_endpoints
from instro.unstable.opcua.types import MessageSecurityMode, SecurityPolicy

endpoint = "opc.tcp://localhost:4840"
for description in get_endpoints(endpoint):
    print(description.endpoint_url, description.security_policy, description.security_mode)

with OpcUaClient.connect(
    endpoint,
    identity=IdentityToken.user_name("operator", os.environ["OPCUA_PASSWORD"]),
    security_policy=SecurityPolicy.BASIC256_SHA256,
    security_mode=MessageSecurityMode.SIGN_AND_ENCRYPT,
    certificate_path="client.der",
    private_key_path="client.pem",
) as client:
    print(client.objects.children)
```

`IdentityToken.x509()` supplies a **user** certificate and key. The connection's
`certificate_path` and `private_key_path` supply the **application** certificate
and key. Provide both together. The [API reference](/ref/protocols/opcua.md)
lists certificate trust controls and all named native connection settings.

The client disconnects when you leave the `with` block. If you open it without a
context manager, call `client.disconnect()` when you finish.

## Navigate from RootFolder

`client.get_node(path)` interprets a string or `RelativePath` from RootFolder.
`client.objects`, `client.types`, and `client.views` expose the standard folders.
`node.get_child(path)` starts at that Node and retains the accumulated path from
RootFolder. Use `node.node_id` to identify a node and `node.browse_path` to inspect its path.

`node.children` returns the immediate children on the same server. If the server
returns references to other servers, the client skips them and issues a
`RuntimeWarning`.

## Read several Nodes at once

`client.read_nodes(nodes)` accepts a mix of Nodes, strings, and `RelativePath`
objects. It resolves all paths from RootFolder in one translation request, then
reads the selected attribute for every Node in one read request. Existing Nodes
must belong to the client. Results follow input order. An empty input returns
`[]` without contacting the server.

```python
from instro.unstable.opcua import OpcUaClient

with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
    clock_sample, state_sample = client.read_nodes([
        "/Objects/Server/ServerStatus/CurrentTime",
        "/Objects/Server/ServerStatus/State",
    ])
```

## Construct paths with multi-digit namespace indexes

For namespace indexes of 10 or higher, construct the path from qualified names.
The string parser can interpret `12:Machine` as the literal name `12:Machine`
in namespace 0.

```python
from instro.unstable.opcua.types import QualifiedName, RelativePath

path = RelativePath.from_qualified_names([
    QualifiedName(0, "Objects"),
    QualifiedName(12, "Machine"),
    QualifiedName(12, "Temperature"),
])
```

Pass this `path` to `client.get_node()` or include it in `client.read_nodes()`.

## Inspect payloads and quality

Attribute reads return `AttributeValue[T]`. Named getters select semantic types:
`node.node_class.value` is a `NodeClass`, `node.display_name.value` is a
`LocalizedText`, and `node.array_dimensions.value` is a `list[int]`, when present.
`node.get_attribute(AttributeId.NODE_ID)` queries the server's NodeId attribute.
`node.node_id` reads the retained identifier.

A sample carries its status and timestamps even if the value is missing or its
type is unsupported. A Bad or Uncertain status can accompany a readable value.
A missing or Null payload yields `None`. Empty strings, byte strings, and arrays
yield `""`, `b""`, and `[]`.

DateTime payloads are integer Unix nanoseconds. To timestamp a measurement,
use `sample.source_unix_timestamp` when the server supplies a source timestamp.
See the [attribute reference](/ref/protocols/opcua.md#supported-values) for the
supported Python types and timestamp fields.

## Write an attribute

Assigning `node.value = 42` writes an OPC UA Int64. If your server expects
an Int16 setpoint, pass `dtype=DataType.INT16` to select that wire type.
Use the path to a writable variable on your server.

```python
from instro.unstable.opcua import OpcUaClient
from instro.unstable.opcua.types import DataType

with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
    setpoint = client.get_node("/Objects/2:Setpoint")
    setpoint.set_attribute("Value", 42, dtype=DataType.INT16)
```

For a list or tuple, the first element selects the wire type. Supply `dtype` to write
an empty list or tuple. See the [write type defaults](/ref/protocols/opcua.md#attribute-writes).

To write an `EnumField` returned by a read, pass the field to the destination node.

```python
from instro.unstable.opcua.types import DataType

node.set_attribute("Value", field, dtype=DataType.ENUM_FIELD)
```

## Iterate subscription samples

Subscribe to the nodes you want to monitor. Each sample pairs its node with
the value and status of the selected attribute.

```python
from instro.unstable.opcua import OpcUaClient

with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
    paths = ["/Objects/Server/ServerStatus/CurrentTime"]
    with client.subscribe(paths, publishing_interval=0.5, poll_interval=2.0) as samples:
        node, sample = next(samples)
        print(node.node_id, sample.status.name())
```

Use `poll_interval` for a fallback read of nodes that sent no notification
during the interval. Omit it to rely on notifications alone. Samples accumulate
if your application consumes them more slowly than they arrive.

Numeric `publishing_interval` and `poll_interval` values are in **seconds**.
Both also accept `datetime.timedelta` or `Duration`. The `sampling_interval`
setting is in **milliseconds**. Leaving the subscription context closes it.
You can also call `samples.close()` explicitly.

## Recovery and shutdown

The client can reconnect after a temporary connection loss. Use
`session_retry_limit` and the retry intervals to configure reconnection.
Operations during an outage can still fail.

**Known defect in subscription cleanup.** If recovery recreates a subscription
with a new server ID, `close()` can raise and leave the replacement active on
the server. If `close()` races with recovery and the server has reassigned the
old ID to another subscription, `close()` can delete that other subscription.
