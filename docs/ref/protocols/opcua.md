# OPC UA

The `instro-opcua` package exposes the synchronous client in
`instro.unstable.opcua` and protocol primitives in `instro.unstable.opcua.types`.
See the [client guide](/library/protocols/opcua.md) for connection, navigation,
subscription, and cleanup examples.

## Client and subscriptions

```{eval-rst}
.. autosummary::
   :toctree: .
   :nosignatures:

   ~instro.unstable.opcua.OpcUaClient
   ~instro.unstable.opcua.Node
   ~instro.unstable.opcua.Subscription
   ~instro.unstable.opcua.IdentityToken

.. autofunction:: instro.unstable.opcua.get_endpoints

.. autoexception:: instro.unstable.opcua.OpcUaError
```

### Connection settings

Every setting is a named keyword of `OpcUaClient.connect()`. Duration settings
accept `Duration`, `datetime.timedelta`, or finite nonnegative numeric **seconds**.
`channel_lifetime` and `session_timeout` instead use integer **milliseconds**.
Defaults and supported fields are listed on the generated client page.

`session_retry_limit=-1` permits unlimited native retries; `0` disables retries.
`max_failed_keep_alive_count=0` disables termination due to that failure count.
`max_chunk_count=0` permits unlimited chunks per message. `max_message_size=0`
means no outgoing message-size limit. Zero string, byte-string, and array lengths
instead permit no elements. Settings retain native retry and restoration semantics,
including the recovery cleanup
[limitation](/library/protocols/opcua.md#recovery-and-shutdown).

Security mode strings are case-insensitive `None`, `Sign`, and `SignAndEncrypt`.
Policy strings are case-insensitive `None`, `Basic128Rsa15`, `Basic256`,
`Basic256Sha256`, `Aes128-Sha256-RsaOaep`, and `Aes256-Sha256-RsaPss`, or their
full OPC Foundation URIs. The AES aliases `Aes128Sha256RsaOaep` and
`Aes256Sha256RsaPss` are also accepted. Typed `MessageSecurityMode` and
`SecurityPolicy` values avoid string parsing.

Wrong types and unknown keywords raise `TypeError`; invalid values raise
`ValueError`. Protocol failures raise `OpcUaError`, whose `status_code` is a
`StatusCode`. Item-level subscription setup failures are grouped in an
`ExceptionGroup`, with each exception retaining `node` and `status_code`.

### Attribute selection and named getters

`Node.get_attribute()` and `subscribe(attribute=...)` accept `AttributeId`, an
integer attribute ID, or a case-insensitive canonical or snake-case name, such as
`"DisplayName"` or `"display_name"`. Omitting the subscription attribute selects
`Value`.

Named getters return `AttributeValue[T]` with the following payload types.
All payloads can also be `None` when absent.

| Getter | Payload type |
|---|---|
| `node_class` | `NodeClass` |
| `browse_name` | `QualifiedName` |
| `display_name`, `description`, `inverse_name` | `LocalizedText` |
| `write_mask`, `user_write_mask` | `AttributeWriteMask` |
| `is_abstract`, `symmetric`, `contains_no_loops`, `historizing`, `executable`, `user_executable` | `bool` |
| `event_notifier` | `EventNotifierType` |
| `value` | Supported scalar or `list[T]` below |
| `data_type` | `NodeId` |
| `value_rank` | `int` |
| `array_dimensions` | `list[int]` |
| `access_level`, `user_access_level` | `AccessLevelType` |
| `minimum_sampling_interval` | `datetime.timedelta` |
| `data_type_definition` | `StructureDefinition` or `EnumDefinition` |
| `role_permissions`, `user_role_permissions` | `list[RolePermissionType]` |
| `access_restrictions` | `AccessRestrictionType` |
| `access_level_ex` | `AccessLevelExType` |

The generic NodeId attribute query also returns `AttributeValue[NodeId]`.
`node.node_id` is local identity and returns `NodeId` directly. Dynamic
`get_attribute()` uses the same semantic conversion as the named getters.

## Supported values

The Value attribute supports these OPC UA scalars and one-dimensional arrays,
represented as Python `list[T]`:

| OPC UA payload | Python representation |
|---|---|
| Boolean | `bool` |
| SByte, Byte, Int16, UInt16, Int32, UInt32, Int64, UInt64 | `int` |
| Float, Double | `float` |
| String, XmlElement | `str` |
| ByteString | `bytes` |
| DateTime | `int` Unix nanoseconds |
| Guid | `uuid.UUID` |
| NodeId, ExpandedNodeId, StatusCode, QualifiedName, LocalizedText | Corresponding wrapper |
| Decoded RolePermissionType, StructureDefinition, StructureField, EnumDefinition, EnumField | Corresponding wrapper |

The structured list is exhaustive. Reading a datatype definition does not add
support for arbitrary values described by that definition. Arbitrary structured
bodies, nested DataValue/Variant payloads, and multidimensional arrays are outside
this payload surface. `AttributeValue.value` reports unsupported payloads with
`BadNotSupported`, and fixed-attribute incompatibility with `BadTypeMismatch`.
It preserves the returned sample's status and timestamps.

`AttributeValue[T]` associates one attribute with payload and quality metadata.
It does not inherit from DataValue and exposes no raw DataValue accessor.
`source_timestamp` and `server_timestamp` are optional `DateTime` wrappers;
`source_picoseconds` and `server_picoseconds` are optional native integer fields
in **10 picoseconds**. The corresponding `*_unix_timestamp` properties return
integer Unix nanoseconds with subnanosecond precision truncated. An absent
source timestamp is not replaced with the server timestamp or vice versa.

```{eval-rst}
.. autosummary::
   :toctree: .
   :nosignatures:

   ~instro.unstable.opcua.types.AttributeValue
   ~instro.unstable.opcua.types.DateTime
   ~instro.unstable.opcua.types.Duration
```

## Identifiers and paths

`NodeId` accepts native textual identifiers, including numeric, string, Guid,
and byte-string forms. Its `identifier` property returns `int`, `str`,
`uuid.UUID`, or `bytes`, respectively. It is an identity value, not a direct
Node lookup input.

`RelativePath.parse()` uses the native path parser, including its known
multi-digit namespace string limitation. `RelativePath.from_qualified_names()`
provides structured hierarchical paths. A Node's `BrowsePath` always starts at
RootFolder and retains the accumulated `relative_path`.

```{eval-rst}
.. autosummary::
   :toctree: .
   :nosignatures:

   ~instro.unstable.opcua.types.NodeId
   ~instro.unstable.opcua.types.ExpandedNodeId
   ~instro.unstable.opcua.types.QualifiedName
   ~instro.unstable.opcua.types.LocalizedText
   ~instro.unstable.opcua.types.RelativePath
   ~instro.unstable.opcua.types.RelativePathElement
   ~instro.unstable.opcua.types.BrowsePath
```

## Semantic and discovery types

Enum and flag wrappers have integer `.value` and `int()` conversion. Constants
use uppercase snake case. String conversion uses the lowercase native variant
name without separators, or its numeric value when no named variant matches.
Flag types expose `NONE`, `&`, `|`, and Boolean
`is_<flag>_set` properties. `StatusCode.code` retains all status bits;
`StatusCode.name()` returns the native subcode name, and `is_good`, `is_uncertain`,
and `is_bad` are properties.

```{eval-rst}
.. autosummary::
   :toctree: .
   :nosignatures:

   ~instro.unstable.opcua.types.AttributeId
   ~instro.unstable.opcua.types.NodeClass
   ~instro.unstable.opcua.types.AttributeWriteMask
   ~instro.unstable.opcua.types.AccessLevelType
   ~instro.unstable.opcua.types.AccessLevelExType
   ~instro.unstable.opcua.types.AccessRestrictionType
   ~instro.unstable.opcua.types.EventNotifierType
   ~instro.unstable.opcua.types.PermissionType
   ~instro.unstable.opcua.types.StatusCode
   ~instro.unstable.opcua.types.StructureType
   ~instro.unstable.opcua.types.RolePermissionType
   ~instro.unstable.opcua.types.StructureField
   ~instro.unstable.opcua.types.StructureDefinition
   ~instro.unstable.opcua.types.EnumField
   ~instro.unstable.opcua.types.EnumDefinition
   ~instro.unstable.opcua.types.MessageSecurityMode
   ~instro.unstable.opcua.types.SecurityPolicy
   ~instro.unstable.opcua.types.ApplicationType
   ~instro.unstable.opcua.types.UserTokenType
   ~instro.unstable.opcua.types.ApplicationDescription
   ~instro.unstable.opcua.types.UserTokenPolicy
   ~instro.unstable.opcua.types.EndpointDescription
```
