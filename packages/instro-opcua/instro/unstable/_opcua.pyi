"""Synchronous Rust bindings over async-opcua 0.19.

Native configuration defaults and status codes pass through unchanged. Returned
DataValues retain Bad and Uncertain quality, including access-denial statuses.
Only failed operations raise OpcUaError; item setup rejections raise ExceptionGroup.
"""

from os import PathLike
from types import TracebackType
from typing import ClassVar, Generic, Iterator, Literal, Self, TypeAlias, TypedDict, TypeVar, Unpack

_T = TypeVar("_T")
_T_co = TypeVar("_T_co", covariant=True)
_Path: TypeAlias = str | PathLike[str]
_UaValue: TypeAlias = (
    bool
    | int
    | float
    | str
    | bytes
    | DateTime
    | Guid
    | NodeId
    | ExpandedNodeId
    | StatusCode
    | QualifiedName
    | LocalizedText
    | ExtensionObject
    | DiagnosticInfo
    | StructureDefinition
    | EnumDefinition
    | RolePermissionType
    | Array[_UaValue]
    | DataValue[_UaValue]
    | None
)

class Duration:
    """Native std::time::Duration; conversions truncate fractional output units.

    Args:
        seconds: Nonnegative whole seconds.
        nanoseconds: Additional nanoseconds, normalized by the native duration.

    Example:
        delay = Duration.from_millis(250)
    """

    def __init__(self, seconds: int, nanoseconds: int = 0) -> None: ...
    @staticmethod
    def from_secs(seconds: int) -> Duration: ...
    @staticmethod
    def from_millis(milliseconds: int) -> Duration: ...
    @staticmethod
    def from_nanos(nanoseconds: int) -> Duration: ...
    def as_secs(self) -> int: ...
    def as_millis(self) -> int: ...
    def as_nanos(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...
    def __lt__(self, other: Duration) -> bool: ...
    def __le__(self, other: Duration) -> bool: ...
    def __gt__(self, other: Duration) -> bool: ...
    def __ge__(self, other: Duration) -> bool: ...

class DateTime:
    """Native OPC UA DateTime, without conversion to Python datetime.

    Args:
        ticks: Native 100-nanosecond ticks since 1601-01-01 UTC.

    Example:
        timestamp = DateTime.parse("2026-01-01T00:00:00.000000100Z")
    """

    def __init__(self, ticks: int) -> None: ...
    @staticmethod
    def parse(text: str) -> DateTime:
        """Parse RFC 3339 using upstream's parser."""
        ...
    def ticks(self) -> int: ...
    def unix_timestamp_nanos(self) -> int:
        """Return integer nanoseconds since 1970-01-01 UTC."""
        ...
    def __eq__(self, other: object) -> bool: ...
    def __lt__(self, other: DateTime) -> bool: ...
    def __le__(self, other: DateTime) -> bool: ...
    def __gt__(self, other: DateTime) -> bool: ...
    def __ge__(self, other: DateTime) -> bool: ...

class AttributeId(Generic[_T]):
    """Wrapped native AttributeId, with its native numeric representation."""

    NODE_ID: ClassVar[AttributeId[NodeId]]
    NODE_CLASS: ClassVar[AttributeId[NodeClass]]
    BROWSE_NAME: ClassVar[AttributeId[QualifiedName]]
    DISPLAY_NAME: ClassVar[AttributeId[LocalizedText]]
    DESCRIPTION: ClassVar[AttributeId[LocalizedText]]
    WRITE_MASK: ClassVar[AttributeId[AttributeWriteMask]]
    USER_WRITE_MASK: ClassVar[AttributeId[AttributeWriteMask]]
    IS_ABSTRACT: ClassVar[AttributeId[bool]]
    SYMMETRIC: ClassVar[AttributeId[bool]]
    INVERSE_NAME: ClassVar[AttributeId[LocalizedText]]
    CONTAINS_NO_LOOPS: ClassVar[AttributeId[bool]]
    EVENT_NOTIFIER: ClassVar[AttributeId[EventNotifierType]]
    VALUE: ClassVar[AttributeId[_UaValue]]
    DATA_TYPE: ClassVar[AttributeId[NodeId]]
    VALUE_RANK: ClassVar[AttributeId[int]]
    ARRAY_DIMENSIONS: ClassVar[AttributeId[Array[int]]]
    ACCESS_LEVEL: ClassVar[AttributeId[AccessLevelType]]
    USER_ACCESS_LEVEL: ClassVar[AttributeId[AccessLevelType]]
    MINIMUM_SAMPLING_INTERVAL: ClassVar[AttributeId[float]]
    HISTORIZING: ClassVar[AttributeId[bool]]
    EXECUTABLE: ClassVar[AttributeId[bool]]
    USER_EXECUTABLE: ClassVar[AttributeId[bool]]
    DATA_TYPE_DEFINITION: ClassVar[AttributeId[StructureDefinition | EnumDefinition | ExtensionObject]]
    ROLE_PERMISSIONS: ClassVar[AttributeId[Array[RolePermissionType]]]
    USER_ROLE_PERMISSIONS: ClassVar[AttributeId[Array[RolePermissionType]]]
    ACCESS_RESTRICTIONS: ClassVar[AttributeId[AccessRestrictionType]]
    ACCESS_LEVEL_EX: ClassVar[AttributeId[AccessLevelExType]]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class NodeClass:
    """Wrapped native NodeClass, with its native numeric representation."""

    UNSPECIFIED: ClassVar[NodeClass]
    OBJECT: ClassVar[NodeClass]
    VARIABLE: ClassVar[NodeClass]
    METHOD: ClassVar[NodeClass]
    OBJECT_TYPE: ClassVar[NodeClass]
    VARIABLE_TYPE: ClassVar[NodeClass]
    REFERENCE_TYPE: ClassVar[NodeClass]
    DATA_TYPE: ClassVar[NodeClass]
    VIEW: ClassVar[NodeClass]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class StructureType:
    """Wrapped native StructureType, with its native numeric representation."""

    STRUCTURE: ClassVar[StructureType]
    STRUCTURE_WITH_OPTIONAL_FIELDS: ClassVar[StructureType]
    UNION: ClassVar[StructureType]
    STRUCTURE_WITH_SUBTYPED_VALUES: ClassVar[StructureType]
    UNION_WITH_SUBTYPED_VALUES: ClassVar[StructureType]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class VariantType:
    """Wrapped native VariantType, with its native numeric representation."""

    BOOLEAN: ClassVar[VariantType]
    SBYTE: ClassVar[VariantType]
    BYTE: ClassVar[VariantType]
    INT16: ClassVar[VariantType]
    UINT16: ClassVar[VariantType]
    INT32: ClassVar[VariantType]
    UINT32: ClassVar[VariantType]
    INT64: ClassVar[VariantType]
    UINT64: ClassVar[VariantType]
    FLOAT: ClassVar[VariantType]
    DOUBLE: ClassVar[VariantType]
    STRING: ClassVar[VariantType]
    DATE_TIME: ClassVar[VariantType]
    GUID: ClassVar[VariantType]
    BYTE_STRING: ClassVar[VariantType]
    XML_ELEMENT: ClassVar[VariantType]
    NODE_ID: ClassVar[VariantType]
    EXPANDED_NODE_ID: ClassVar[VariantType]
    STATUS_CODE: ClassVar[VariantType]
    QUALIFIED_NAME: ClassVar[VariantType]
    LOCALIZED_TEXT: ClassVar[VariantType]
    EXTENSION_OBJECT: ClassVar[VariantType]
    DATA_VALUE: ClassVar[VariantType]
    VARIANT: ClassVar[VariantType]
    DIAGNOSTIC_INFO: ClassVar[VariantType]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class AttributeWriteMask:
    """Wrapped native AttributeWriteMask, with its native numeric representation."""

    ACCESS_LEVEL: ClassVar[AttributeWriteMask]
    ARRAY_DIMENSIONS: ClassVar[AttributeWriteMask]
    BROWSE_NAME: ClassVar[AttributeWriteMask]
    CONTAINS_NO_LOOPS: ClassVar[AttributeWriteMask]
    DATA_TYPE: ClassVar[AttributeWriteMask]
    DESCRIPTION: ClassVar[AttributeWriteMask]
    DISPLAY_NAME: ClassVar[AttributeWriteMask]
    EVENT_NOTIFIER: ClassVar[AttributeWriteMask]
    EXECUTABLE: ClassVar[AttributeWriteMask]
    HISTORIZING: ClassVar[AttributeWriteMask]
    INVERSE_NAME: ClassVar[AttributeWriteMask]
    IS_ABSTRACT: ClassVar[AttributeWriteMask]
    MINIMUM_SAMPLING_INTERVAL: ClassVar[AttributeWriteMask]
    NODE_CLASS: ClassVar[AttributeWriteMask]
    NODE_ID: ClassVar[AttributeWriteMask]
    SYMMETRIC: ClassVar[AttributeWriteMask]
    USER_ACCESS_LEVEL: ClassVar[AttributeWriteMask]
    USER_EXECUTABLE: ClassVar[AttributeWriteMask]
    USER_WRITE_MASK: ClassVar[AttributeWriteMask]
    VALUE_RANK: ClassVar[AttributeWriteMask]
    WRITE_MASK: ClassVar[AttributeWriteMask]
    VALUE_FOR_VARIABLE_TYPE: ClassVar[AttributeWriteMask]
    DATA_TYPE_DEFINITION: ClassVar[AttributeWriteMask]
    ROLE_PERMISSIONS: ClassVar[AttributeWriteMask]
    ACCESS_RESTRICTIONS: ClassVar[AttributeWriteMask]
    ACCESS_LEVEL_EX: ClassVar[AttributeWriteMask]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class AccessLevelType:
    """Wrapped native AccessLevelType, with its native numeric representation."""

    CURRENT_READ: ClassVar[AccessLevelType]
    CURRENT_WRITE: ClassVar[AccessLevelType]
    HISTORY_READ: ClassVar[AccessLevelType]
    HISTORY_WRITE: ClassVar[AccessLevelType]
    SEMANTIC_CHANGE: ClassVar[AccessLevelType]
    STATUS_WRITE: ClassVar[AccessLevelType]
    TIMESTAMP_WRITE: ClassVar[AccessLevelType]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class AccessLevelExType:
    """Wrapped native AccessLevelExType, with its native numeric representation."""

    CURRENT_READ: ClassVar[AccessLevelExType]
    CURRENT_WRITE: ClassVar[AccessLevelExType]
    HISTORY_READ: ClassVar[AccessLevelExType]
    HISTORY_WRITE: ClassVar[AccessLevelExType]
    SEMANTIC_CHANGE: ClassVar[AccessLevelExType]
    STATUS_WRITE: ClassVar[AccessLevelExType]
    TIMESTAMP_WRITE: ClassVar[AccessLevelExType]
    NONATOMIC_READ: ClassVar[AccessLevelExType]
    NONATOMIC_WRITE: ClassVar[AccessLevelExType]
    WRITE_FULL_ARRAY_ONLY: ClassVar[AccessLevelExType]
    NO_SUB_DATA_TYPES: ClassVar[AccessLevelExType]
    NON_VOLATILE: ClassVar[AccessLevelExType]
    CONSTANT: ClassVar[AccessLevelExType]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class AccessRestrictionType:
    """Wrapped native AccessRestrictionType, with its native numeric representation."""

    SIGNING_REQUIRED: ClassVar[AccessRestrictionType]
    ENCRYPTION_REQUIRED: ClassVar[AccessRestrictionType]
    SESSION_REQUIRED: ClassVar[AccessRestrictionType]
    APPLY_RESTRICTIONS_TO_BROWSE: ClassVar[AccessRestrictionType]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class EventNotifierType:
    """Wrapped native EventNotifierType, with its native numeric representation."""

    SUBSCRIBE_TO_EVENTS: ClassVar[EventNotifierType]
    HISTORY_READ: ClassVar[EventNotifierType]
    HISTORY_WRITE: ClassVar[EventNotifierType]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class PermissionType:
    """Wrapped native PermissionType, with its native numeric representation."""

    BROWSE: ClassVar[PermissionType]
    READ_ROLE_PERMISSIONS: ClassVar[PermissionType]
    WRITE_ATTRIBUTE: ClassVar[PermissionType]
    WRITE_ROLE_PERMISSIONS: ClassVar[PermissionType]
    WRITE_HISTORIZING: ClassVar[PermissionType]
    READ: ClassVar[PermissionType]
    WRITE: ClassVar[PermissionType]
    READ_HISTORY: ClassVar[PermissionType]
    INSERT_HISTORY: ClassVar[PermissionType]
    MODIFY_HISTORY: ClassVar[PermissionType]
    DELETE_HISTORY: ClassVar[PermissionType]
    RECEIVE_EVENTS: ClassVar[PermissionType]
    CALL: ClassVar[PermissionType]
    ADD_REFERENCE: ClassVar[PermissionType]
    REMOVE_REFERENCE: ClassVar[PermissionType]
    DELETE_NODE: ClassVar[PermissionType]
    ADD_NODE: ClassVar[PermissionType]

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class StatusCode:
    """The unchanged 32-bit upstream status code, including information bits."""

    def __init__(self, value: int) -> None: ...
    @property
    def value(self) -> int: ...
    @property
    def is_good(self) -> bool: ...
    @property
    def is_bad(self) -> bool: ...
    @property
    def is_uncertain(self) -> bool: ...
    def __int__(self) -> int: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class Guid:
    """Native OPC UA GUID, parsed from its string representation."""

    def __init__(self, text: str) -> None: ...
    def __str__(self) -> str: ...

class NodeId:
    """Native NodeId, parsed using upstream's NodeId syntax."""

    def __init__(self, text: str) -> None: ...
    @property
    def namespace_index(self) -> int: ...
    @property
    def identifier(self) -> int | str | Guid | bytes | None: ...
    def __str__(self) -> str: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class QualifiedName:
    def __init__(self, namespace_index: int, name: str) -> None: ...
    @property
    def namespace_index(self) -> int: ...
    @property
    def name(self) -> str | None: ...
    def __eq__(self, other: object) -> bool: ...

class LocalizedText:
    def __init__(self, text: str | None = None, locale: str | None = None) -> None: ...
    @property
    def text(self) -> str | None: ...
    @property
    def locale(self) -> str | None: ...
    def __eq__(self, other: object) -> bool: ...

class RelativePath:
    """Upstream relative path with native parsing and escaping.

    Args:
        text: Native path, for example ``/2:Machine/2:Temperature``.

    The 0.19.0 string parser misparses multi-digit target namespace indexes.
    Use from_qualified_names for those indexes; no replacement parser is added.
    """

    def __init__(self, text: str) -> None: ...
    @staticmethod
    def from_qualified_names(names: list[QualifiedName]) -> RelativePath: ...
    def __str__(self) -> str: ...

class Array(Generic[_T_co]):
    """Native flattened array or matrix with upstream's type and dimensions.

    Empty-array metadata already discarded by upstream cannot be reconstructed.
    Indexed access supports negative indexes. The values getter returns a list.
    """

    @property
    def element_type(self) -> VariantType: ...
    @property
    def dimensions(self) -> list[int] | None: ...
    @property
    def values(self) -> list[_T_co]: ...
    def __len__(self) -> int: ...
    def __getitem__(self, index: int) -> _T_co: ...

class DataValue(Generic[_T_co]):
    """Payload and unmodified native quality and timestamp metadata.

    Missing fields remain None. A missing wire status implies Good in OPC UA,
    but the status getter preserves the absent native field. Bad and Uncertain
    quality do not make payload access raise an exception.

    Unix timestamp getters return base Unix nanoseconds plus picoseconds // 100.
    Missing fractions contribute zero, and missing timestamps produce None.
    """

    @property
    def value(self) -> _T_co | None: ...
    @property
    def status(self) -> StatusCode | None: ...
    @property
    def source_timestamp(self) -> DateTime | None: ...
    @property
    def server_timestamp(self) -> DateTime | None: ...
    @property
    def source_picoseconds(self) -> int | None:
        """Native count of 10-picosecond intervals, without normalization."""
        ...
    @property
    def server_picoseconds(self) -> int | None:
        """Native count of 10-picosecond intervals, without normalization."""
        ...
    @property
    def source_unix_timestamp(self) -> int | None: ...
    @property
    def server_unix_timestamp(self) -> int | None: ...

class ExtensionObject:
    """Raw native structured value, retaining its identifier and encoded body.

    Args:
        type_id: Native binary encoding identifier.
        body: Encoded body bytes.
    """

    def __init__(self, type_id: NodeId, body: bytes) -> None: ...
    @property
    def type_id(self) -> ExpandedNodeId: ...
    @property
    def encoding(self) -> Literal["none", "binary", "xml"]: ...
    @property
    def body(self) -> bytes | None: ...

class ExpandedNodeId:
    @property
    def node_id(self) -> NodeId: ...
    @property
    def namespace_uri(self) -> str | None: ...
    @property
    def server_index(self) -> int: ...

class BrowsePath:
    @property
    def starting_node(self) -> NodeId: ...
    @property
    def relative_path(self) -> RelativePath: ...

class RolePermissionType:
    @property
    def role_id(self) -> NodeId: ...
    @property
    def permissions(self) -> PermissionType: ...

class StructureField:
    @property
    def name(self) -> str | None: ...
    @property
    def description(self) -> LocalizedText: ...
    @property
    def data_type(self) -> NodeId: ...
    @property
    def value_rank(self) -> int: ...
    @property
    def array_dimensions(self) -> list[int] | None: ...
    @property
    def max_string_length(self) -> int: ...
    @property
    def is_optional(self) -> bool: ...

class StructureDefinition:
    @property
    def default_encoding_id(self) -> NodeId: ...
    @property
    def base_data_type(self) -> NodeId: ...
    @property
    def structure_type(self) -> StructureType: ...
    @property
    def fields(self) -> list[StructureField] | None: ...

class EnumField:
    @property
    def name(self) -> str | None: ...
    @property
    def value(self) -> int: ...
    @property
    def display_name(self) -> LocalizedText: ...
    @property
    def description(self) -> LocalizedText: ...

class EnumDefinition:
    @property
    def fields(self) -> list[EnumField] | None: ...

class DiagnosticInfo:
    @property
    def symbolic_id(self) -> int | None: ...
    @property
    def namespace_uri(self) -> int | None: ...
    @property
    def locale(self) -> int | None: ...
    @property
    def localized_text(self) -> int | None: ...
    @property
    def additional_info(self) -> str | None: ...
    @property
    def inner_status_code(self) -> StatusCode | None: ...
    @property
    def inner_diagnostic_info(self) -> DiagnosticInfo | None: ...

class IdentityToken:
    """Native user identity, separate from the application certificate."""

    @staticmethod
    def anonymous() -> IdentityToken: ...
    @staticmethod
    def user_name(username: str, password: str) -> IdentityToken: ...
    @staticmethod
    def x509(certificate_path: _Path, private_key_path: _Path) -> IdentityToken: ...

class ClientEndpoint:
    """Native configured endpoint for config-based connection selection."""

    def __init__(
        self,
        url: str,
        *,
        security_policy: str = "None",
        security_mode: str = "None",
        user_token_id: str = "ANONYMOUS",
    ) -> None: ...

class ClientUserToken:
    @staticmethod
    def user_pass(username: str, password: str) -> ClientUserToken: ...
    @staticmethod
    def x509(username: str, certificate_path: _Path, private_key_path: _Path) -> ClientUserToken: ...

class _ClientSettings(TypedDict, total=False):
    config_file: _Path
    application_name: str
    application_uri: str
    product_uri: str
    create_sample_keypair: bool
    certificate_path: _Path
    private_key_path: _Path
    trust_server_certs: bool
    verify_server_certs: bool
    pki_dir: _Path
    preferred_locales: list[str]
    default_endpoint: str
    endpoint: tuple[str, ClientEndpoint]
    endpoints: list[tuple[str, ClientEndpoint]]
    user_token: tuple[str, ClientUserToken]
    channel_lifetime: int
    session_retry_limit: int
    session_retry_initial: Duration
    session_retry_max: Duration
    keep_alive_interval: Duration
    max_array_length: int
    max_byte_string_length: int
    max_chunk_count: int
    max_chunk_size: int
    max_incoming_chunk_size: int
    max_message_size: int
    max_string_length: int
    max_failed_keep_alive_count: int
    request_timeout: Duration
    publish_timeout: Duration
    min_publish_interval: Duration
    ignore_clock_skew: bool
    recreate_monitored_items_chunk: int
    recreate_subscriptions: bool
    session_name: str
    session_timeout: int
    session_nonce_length: int

class MonitoredItemSettings:
    """Native per-item requests within the group's single selected attribute.

    Args:
        sampling_interval: Milliseconds. Negative inherits the publishing interval;
            zero requests the fastest practical sampling.
        queue_size: Server sample queue size; zero or one requests a single value.
        discard_oldest: Native server queue discard policy.
        client_handle: Native client handle; zero lets upstream assign a handle.
        filter: Native encoded monitoring filter, or no filter.
        monitoring_mode: Native mode: 0 disabled, 1 sampling, 2 reporting.
        index_range: Native NumericRange syntax; None selects the complete value.
        data_encoding: Native qualified encoding name; None selects the default.
    """

    def __init__(
        self,
        *,
        sampling_interval: float = 0.0,
        queue_size: int = 0,
        discard_oldest: bool = False,
        client_handle: int = 0,
        filter: ExtensionObject | None = None,
        monitoring_mode: int = 2,
        index_range: str | None = None,
        data_encoding: QualifiedName | None = None,
    ) -> None: ...

class OpcUaClient:
    """Synchronous client using upstream's connection and recovery behavior.

    Example:
        with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
            sample = client.objects.get_child("/2:Temperature").value
    """

    @staticmethod
    def connect(
        endpoint_url: str | None = None,
        *,
        identity: IdentityToken | None = None,
        security_policy: str = "None",
        security_mode: int = 1,
        **settings: Unpack[_ClientSettings],
    ) -> OpcUaClient:
        """Connect with native builder settings and defaults.

        Args:
            endpoint_url: Endpoint discovery URL. None selects the configured default.
            identity: Anonymous, username/password, or X.509 user identity.
            security_policy: Native policy name or URI.
            security_mode: Native message mode: 1 None, 2 Sign, 3 SignAndEncrypt.
            settings: Optional upstream builder settings. config_file supplies the
                native initial configuration, with explicit settings applied after it.

        channel_lifetime and session_timeout use native milliseconds.
        session_retry_limit is -1 for unlimited retries, zero for none, or positive.
        Duration settings retain native precision. Message limits are native counts
        or bytes, with native zero-limit semantics. Certificate paths are relative
        to pki_dir. The reserved ANONYMOUS user token cannot be overridden.

        Raises:
            ValueError: Uncoded native configuration failure.
            OpcUaError: Coded native operation failure.
        """
        ...
    def disconnect(self) -> None: ...
    def enable_reconnects(self) -> None: ...
    def disable_reconnects(self) -> None: ...
    def get_node(self, path: NodeId | RelativePath | str) -> Node:
        """Return a local NodeId handle or translate a path from the Root Node."""
        ...
    @property
    def objects(self) -> Node: ...
    @property
    def types(self) -> Node: ...
    @property
    def views(self) -> Node: ...
    def subscribe(
        self,
        nodes: list[Node],
        *,
        attribute: AttributeId[_T],
        publishing_interval: Duration | None = None,
        lifetime_count: int = 60,
        max_keep_alive_count: int = 20,
        max_notifications_per_publish: int = 0,
        priority: int = 0,
        publishing_enabled: bool = True,
        sampling_interval: float = 0.0,
        queue_size: int = 0,
        discard_oldest: bool = False,
        poll_interval: Duration | None = None,
        item_settings: list[MonitoredItemSettings] | None = None,
    ) -> Subscription[_T]:
        """Establish all items before returning a synchronous subscription group.

        NodeIds must be unique within this group and belong to this client.
        All Nodes use the selected standard attribute. item_settings, when supplied,
        must contain one entry per Node, in request order, and replaces the common
        sampling_interval, queue_size, and discard_oldest settings.

        publishing_interval defaults to the native 500 ms request. Lifetime and
        keepalive counts are publishing cycles, and notification limits are counts;
        zero max_notifications_per_publish means no limit. Priority is a byte.
        The server may revise these requests. Sampling intervals are milliseconds;
        negative inherits publishing and zero requests fastest practical sampling.
        Server queue_size is independent of the unbounded Python delivery queue.

        None disables background polling. A positive Duration polls quiet Nodes
        for the selected attribute. The first tick follows one interval; missed
        ticks are skipped. Pending polls reconcile with notifications before delivery.

        Raises:
            ValueError: Duplicate Nodes, a zero polling interval, or invalid settings.
            ExceptionGroup: One OpcUaError per rejected item, in request order,
                including when only one item fails. Each error exposes its Node.
            OpcUaError: A service-level setup failure.

        Failed setup attempts server deletion before raising the original failure.
        A cleanup failure remains available through the original exception context.
        """
        ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]: ...

class Node:
    """Local identity plus synchronous navigation and standard attribute reads.

    Server getters always return DataValue, including absent or Bad payloads.
    """

    @property
    def node_id(self) -> NodeId:
        """Local NodeId; this getter performs no server read."""
        ...
    @property
    def browse_path(self) -> BrowsePath: ...
    def get_attribute(self, attribute: AttributeId[_T]) -> DataValue[_T]: ...
    def get_child(self, path: RelativePath | str) -> Node: ...
    @property
    def children(self) -> list[Node]: ...
    def browse_children(self, *, max_references_per_node: int = 0) -> list[Node]:
        """Enumerate all immediate children, following native continuation pages.

        Zero requests the server's default page size; a positive value requests a
        per-page reference limit, without limiting the returned child count.
        """
        ...
    @property
    def node_class(self) -> DataValue[NodeClass]: ...
    @property
    def browse_name(self) -> DataValue[QualifiedName]: ...
    @property
    def display_name(self) -> DataValue[LocalizedText]: ...
    @property
    def description(self) -> DataValue[LocalizedText]: ...
    @property
    def write_mask(self) -> DataValue[AttributeWriteMask]: ...
    @property
    def user_write_mask(self) -> DataValue[AttributeWriteMask]: ...
    @property
    def is_abstract(self) -> DataValue[bool]: ...
    @property
    def symmetric(self) -> DataValue[bool]: ...
    @property
    def inverse_name(self) -> DataValue[LocalizedText]: ...
    @property
    def contains_no_loops(self) -> DataValue[bool]: ...
    @property
    def event_notifier(self) -> DataValue[EventNotifierType]: ...
    @property
    def value(self) -> DataValue[_UaValue]: ...
    @property
    def data_type(self) -> DataValue[NodeId]: ...
    @property
    def value_rank(self) -> DataValue[int]: ...
    @property
    def array_dimensions(self) -> DataValue[Array[int]]: ...
    @property
    def access_level(self) -> DataValue[AccessLevelType]: ...
    @property
    def user_access_level(self) -> DataValue[AccessLevelType]: ...
    @property
    def minimum_sampling_interval(self) -> DataValue[float]: ...
    @property
    def historizing(self) -> DataValue[bool]: ...
    @property
    def executable(self) -> DataValue[bool]: ...
    @property
    def user_executable(self) -> DataValue[bool]: ...
    @property
    def data_type_definition(self) -> DataValue[StructureDefinition | EnumDefinition | ExtensionObject]: ...
    @property
    def role_permissions(self) -> DataValue[Array[RolePermissionType]]: ...
    @property
    def user_role_permissions(self) -> DataValue[Array[RolePermissionType]]: ...
    @property
    def access_restrictions(self) -> DataValue[AccessRestrictionType]: ...
    @property
    def access_level_ex(self) -> DataValue[AccessLevelExType]: ...

class Subscription(Generic[_T_co], Iterator[tuple[Node, DataValue[_T_co]]]):
    """Interruptible blocking iterator with an unbounded delivery queue.

    Normal completion drains queued output then stops. Native failures drain output
    then raise OpcUaError, retaining upstream's completion/error classification.
    """

    def __iter__(self) -> Self: ...
    def __next__(self) -> tuple[Node, DataValue[_T_co]]: ...
    def close(self) -> None:
        """Stop production, flush polls, preserve output, and await native deletion.

        Buffered output remains readable. An already pending native failure is
        reported after draining; otherwise iteration stops. A deletion error raises
        here and is not inserted as an additional iterator error. Repeated close
        calls have no effect.
        """
        ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Close while retaining a context-body exception as the primary exception.

        A simultaneous cleanup failure remains inspectable as its exception context.
        """
        ...

class OpcUaError(Exception):
    """Rust-backed operation failure with read-only native status and Node fields."""

    def __init__(self, message: str, status_code: StatusCode, node: Node | None = None) -> None: ...
    @property
    def status_code(self) -> StatusCode: ...
    @property
    def node(self) -> Node | None: ...
