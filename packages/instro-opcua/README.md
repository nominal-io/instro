# instro-opcua

OPC UA protocol primitives in `instro.unstable.opcua.types`.

Use `instro.unstable.opcua` to discover endpoints and connect to a server.
Connections use anonymous authentication by default. Pass an `IdentityToken`
to authenticate with a username or certificate.

Discovery and connection use `instro/opcua` under your local data directory for the certificate store.
On Linux the base directory is `$XDG_DATA_HOME` or `~/.local/share`.
On macOS it is `~/Library/Application Support`, and on Windows it is `%LOCALAPPDATA%`.
If the operating system provides no local data directory, the client uses `./pki`.
Pass `pki_dir` to `get_endpoints()` to use a different certificate store for discovery.

Use Nodes for navigation, attribute access, and ordered batch reads.

Assign to `node.value` or `node.display_name` to write the corresponding
attribute. A Value write selects its wire type from the Python value and sends
one Write request. A Python `int` selects Int64, an `IntEnum` selects Int32,
and a `float` selects Double. A list or tuple takes its wire type from its first element.
Supply `dtype` for an empty list or tuple, or when the server expects a specific type,
for example `node.set_attribute("Value", 42, dtype=DataType.INT16)`.
Import `DataType` from `instro.unstable.opcua.types`.
Write an `EnumField` returned by a read with `node.set_attribute("Value", field, dtype=DataType.ENUM_FIELD)`.
Use `UUID` for Guid values, `XmlElement("<mode>on</mode>")` for XML values,
and `dtype=DataType.DATE_TIME` with integer Unix nanoseconds in multiples of 100 for DateTime values.
XML reads return `XmlElement` objects. Use `str(value)` to retrieve the XML text.
Ctrl-C can stop Python waiting. A submitted write may still take effect.

Subscriptions retain notifications and support optional fallback polling.
