# instro-opcua

Synchronous OPC UA client primitives for [instro](https://github.com/nominal-io/instro).
Python 3.11 through 3.14 are supported. The API lives under `instro.unstable.opcua`.

```bash
pip install instro-opcua
```

```python
from instro.unstable.opcua import OpcUaClient

with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
    clock = client.objects.get_child("/Server/ServerStatus/CurrentTime")
    sample = clock.value
    print(sample.value, sample.status.name(), sample.source_unix_timestamp)

    with client.subscribe([clock], publishing_interval=0.5) as samples:
        for node, sample in samples:
            print(node.node_id, sample.value)
            break
```

Browse paths start at RootFolder; child navigation retains the full path. Reads
and subscription samples return `AttributeValue[T]`, retaining quality and timestamp
metadata. Subscriptions consume an iterable once and yield `(Node, AttributeValue)`
pairs and support optional fallback polling. Use contexts or explicit `close()` /
`disconnect()` for synchronous cleanup.

Assign to `node.value` or `node.display_name` to write the corresponding
attribute. A Value assignment reads the node's declared data type. Use
`node.set_attribute("Value", 42, dtype=DataType.INT16)` to select the wire type
and skip that read. Import `DataType` from `instro.unstable.opcua.types`.
Use `UUID` for Guid values, `XmlElement("<mode>on</mode>")` for XML values,
and integer Unix nanoseconds in multiples of 100 for DateTime values.
XML reads return `XmlElement` objects. Use `str(value)` to retrieve the XML text.
Ctrl-C can stop Python waiting. A submitted write may still take effect.

The native `async-opcua` 0.19 backend controls reconnection. Its known subscription
restoration/deletion coordination limitation can affect remote cleanup during
recovery; local production still stops and queued output remains available.

See the [client guide](https://instro.nominal.io/library/protocols/opcua/)
for authentication, signal handling, supported values, and recovery limits, and
the [API reference](https://instro.nominal.io/ref/protocols/opcua/)
for named connection and subscription settings.

Development setup and checks are documented in
[CONTRIBUTING.md](https://github.com/nominal-io/instro/blob/main/CONTRIBUTING.md).
