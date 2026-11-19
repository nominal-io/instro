# instro-opcua

OPC UA protocol primitives in `instro.unstable.opcua.types`.

Anonymous client connection and discovery are available from `instro.unstable.opcua`.

Connection settings and `IdentityToken` configure authentication and security.

Use Nodes for navigation, attribute access, and ordered batch reads.

Assign to `node.value` or `node.display_name` to write the corresponding
attribute. A Value assignment reads the node's declared data type. Use
`node.set_attribute("Value", 42, dtype=DataType.INT16)` to select the wire type
and skip that read. Import `DataType` from `instro.unstable.opcua.types`.
Use `UUID` for Guid values, `XmlElement("<mode>on</mode>")` for XML values,
and integer Unix nanoseconds in multiples of 100 for DateTime values.
XML reads return `XmlElement` objects. Use `str(value)` to retrieve the XML text.
Ctrl-C can stop Python waiting. A submitted write may still take effect.

Subscriptions retain notifications and support optional fallback polling.
