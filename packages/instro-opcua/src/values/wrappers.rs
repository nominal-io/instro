use std::hash::DefaultHasher;
use std::hash::Hash as _;
use std::hash::Hasher as _;
use std::str::FromStr;

use opcua::types as ua;
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyAny;
use pyo3::types::PyBytes;

use super::UaType;

macro_rules! ua_enum {
    ($(#[$attrs:meta])* $name:ident $($generic:ident)? ($native:ty, $repr:ty) {
        from($input:ident) => $construct:expr;
        value($inner:ident) => $bits:expr;
        $($variant:ident),* $(,)?
    }) => {
        /// A semantic OPC UA integer value with typed constants and equality.
        ///
        /// Construct a semantic value from its native integer representation.
        ///
        /// Args:
        ///     value (int): Native numeric value or flag bits.
        ///
        /// Returns:
        ///     object: The corresponding typed wrapper.
        ///
        /// Examples:
        ///     >>> from instro.unstable.opcua.types import AttributeId
        ///     >>> AttributeId(13) == AttributeId.VALUE
        ///     True
        #[pyclass(frozen, from_py_object, eq, module = "instro.unstable.opcua.types")]
        #[derive(Clone, Copy, PartialEq)]
        pub(crate) struct $name($native);

        impl UaType for $name {
            type Inner = $native;

            fn new(value: Self::Inner) -> Self {
                Self(value)
            }

            fn as_ua_ref(&self) -> &Self::Inner {
                &self.0
            }
        }

        paste::paste! {
            #[pymethods]
            impl $name {
                #[new]
                fn new($input: $repr) -> PyResult<Self> {
                    let value: PyResult<$native> = $construct;
                    value.map(Self)
                }

                #[getter]
                fn value(&self) -> $repr {
                    let $inner = self.0;
                    $bits
                }

                fn __int__(&self) -> $repr {
                    self.value()
                }

                fn __hash__(&self) -> i64 {
                    i64::from(self.value())
                }

                fn __str__(&self) -> String {
                    $(
                        if self.0 == Self::[<$variant:snake:upper>].0 {
                            return stringify!($variant).to_ascii_lowercase();
                        }
                    )*

                    self.value().to_string()
                }

                fn __repr__(&self) -> String {
                    format!("{}({})", stringify!($name), self.value())
                }

                $(
                    #[classattr]
                    pub const [<$variant:snake:upper>]: Self = Self(<$native>::$variant);
                )*
            }
        }

        ua_enum!(@flags $name $($generic)?; $($variant),*);
    };

    (@flags $name:ident flags; $($variant:ident),*) => {
        paste::paste! {
            #[pymethods]
            impl $name {
                #[classattr]
                const NONE: Self = Self(<Self as UaType>::Inner::empty());

                fn __and__(&self, other: &Self) -> Self {
                    Self(self.0 & other.0)
                }

                fn __or__(&self, other: &Self) -> Self {
                    Self(self.0 | other.0)
                }

                $(
                    #[getter]
                    fn [<is_ $variant:snake _set>](&self) -> bool {
                        self.0.contains(Self::[<$variant:snake:upper>].0)
                    }
                )*
            }
        }
    };

    (@flags $name:ident $($other:ident)?; $($variant:ident),*) => {};
}

ua_enum!(AttributeId(ua::AttributeId, u32) {
    from(value) => ua::AttributeId::from_u32(value)
        .map_err(|error| PyValueError::new_err(error.to_string()));

    value(inner) => inner as u32;

    NodeId,
    NodeClass,
    BrowseName,
    DisplayName,
    Description,
    WriteMask,
    UserWriteMask,
    IsAbstract,
    Symmetric,
    InverseName,
    ContainsNoLoops,
    EventNotifier,
    Value,
    DataType,
    ValueRank,
    ArrayDimensions,
    AccessLevel,
    UserAccessLevel,
    MinimumSamplingInterval,
    Historizing,
    Executable,
    UserExecutable,
    DataTypeDefinition,
    RolePermissions,
    UserRolePermissions,
    AccessRestrictions,
    AccessLevelEx,
});

ua_enum!(NodeClass(ua::NodeClass, i32) {
    from(value) => ua::NodeClass::try_from(value).map_err(crate::error::native);
    value(inner) => inner as i32;

    Unspecified,
    Object,
    Variable,
    Method,
    ObjectType,
    VariableType,
    ReferenceType,
    DataType,
    View,
});

ua_enum!(StructureType(ua::StructureType, i32) {
    from(value) => ua::StructureType::try_from(value).map_err(crate::error::native);
    value(inner) => inner as i32;

    Structure,
    StructureWithOptionalFields,
    Union,
    StructureWithSubtypedValues,
    UnionWithSubtypedValues,
});

ua_enum!(AttributeWriteMask flags(ua::AttributeWriteMask, i32) {
    from(value) => Ok(ua::AttributeWriteMask::from_bits_retain(value));
    value(inner) => inner.bits();

    AccessLevel,
    ArrayDimensions,
    BrowseName,
    ContainsNoLoops,
    DataType,
    Description,
    DisplayName,
    EventNotifier,
    Executable,
    Historizing,
    InverseName,
    IsAbstract,
    MinimumSamplingInterval,
    NodeClass,
    NodeId,
    Symmetric,
    UserAccessLevel,
    UserExecutable,
    UserWriteMask,
    ValueRank,
    WriteMask,
    ValueForVariableType,
    DataTypeDefinition,
    RolePermissions,
    AccessRestrictions,
    AccessLevelEx,
});

ua_enum!(AccessLevelType flags(ua::AccessLevelType, u8) {
    from(value) => Ok(ua::AccessLevelType::from_bits_retain(value));
    value(inner) => inner.bits();

    CurrentRead,
    CurrentWrite,
    HistoryRead,
    HistoryWrite,
    SemanticChange,
    StatusWrite,
    TimestampWrite,
});

ua_enum!(AccessLevelExType flags(ua::AccessLevelExType, i32) {
    from(value) => Ok(ua::AccessLevelExType::from_bits_retain(value));
    value(inner) => inner.bits();

    CurrentRead,
    CurrentWrite,
    HistoryRead,
    HistoryWrite,
    SemanticChange,
    StatusWrite,
    TimestampWrite,
    NonatomicRead,
    NonatomicWrite,
    WriteFullArrayOnly,
    NoSubDataTypes,
    NonVolatile,
    Constant,
});

ua_enum!(AccessRestrictionType flags(ua::AccessRestrictionType, i16) {
    from(value) => Ok(ua::AccessRestrictionType::from_bits_retain(value));
    value(inner) => inner.bits();

    SigningRequired,
    EncryptionRequired,
    SessionRequired,
    ApplyRestrictionsToBrowse,
});

ua_enum!(EventNotifierType flags(ua::EventNotifierType, u8) {
    from(value) => Ok(ua::EventNotifierType::from_bits_retain(value));
    value(inner) => inner.bits();

    SubscribeToEvents,
    HistoryRead,
    HistoryWrite,
});

ua_enum!(PermissionType flags(ua::PermissionType, i32) {
    from(value) => Ok(ua::PermissionType::from_bits_retain(value));
    value(inner) => inner.bits();

    Browse,
    ReadRolePermissions,
    WriteAttribute,
    WriteRolePermissions,
    WriteHistorizing,
    Read,
    Write,
    ReadHistory,
    InsertHistory,
    ModifyHistory,
    DeleteHistory,
    ReceiveEvents,
    Call,
    AddReference,
    RemoveReference,
    DeleteNode,
    AddNode,
});

macro_rules! ua_struct {
    ($(#[$attrs:meta])* pub struct $this_t:ident ord($compare:path) { $($fields:tt)* }) => {
        $(#[$attrs])*
        #[pyclass(frozen, from_py_object, eq, ord, module = "instro.unstable.opcua.types")]
        #[derive(Clone)]
        pub(crate) struct $this_t(ua::$this_t);

        impl PartialEq for $this_t {
            fn eq(&self, other: &Self) -> bool {
                $compare(&self.0, &other.0).is_eq()
            }
        }

        impl PartialOrd for $this_t {
            fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
                Some($compare(&self.0, &other.0))
            }
        }

        ua_struct!(@methods $this_t { $($fields)* });
    };

    ($(#[$attrs:meta])* pub struct $this_t:ident { $($fields:tt)* }) => {
        $(#[$attrs])*
        #[pyclass(frozen, from_py_object, eq, module = "instro.unstable.opcua.types")]
        #[derive(Clone, PartialEq)]
        pub(crate) struct $this_t(ua::$this_t);

        ua_struct!(@methods $this_t { $($fields)* });
    };

    (
        @methods $this_t:ident {
            $($(#[$field_attrs:meta])*
            $field:ident: $ty:ty = |$value:ident| $convert:expr),* $(,)?
        }
    ) => {
        impl UaType for $this_t {
            type Inner = ua::$this_t;

            fn new(value: Self::Inner) -> Self {
                Self(value)
            }

            fn as_ua_ref(&self) -> &Self::Inner {
                &self.0
            }
        }

        #[pymethods]
        impl $this_t {
            $(
                $(#[$field_attrs])*
                #[getter]
                fn $field(&self) -> $ty {
                    let $value = self.as_ua_ref();
                    $convert
                }
            )*
        }
    };
}

ua_struct! {
    /// A role identifier and its PermissionType flags.
    pub struct RolePermissionType {
        role_id: NodeId = |value| NodeId(value.role_id.clone()),
        permissions: PermissionType = |value| PermissionType(value.permissions),
    }
}

ua_struct! {
    /// One field in a StructureDefinition, including type and array metadata.
    pub struct StructureField ord(super::ordering::structure_field) {
        name: String = |value| value.name.as_ref().to_owned(),
        description: LocalizedText = |value| LocalizedText(value.description.clone()),
        data_type: NodeId = |value| NodeId(value.data_type.clone()),
        value_rank: i32 = |value| value.value_rank,
        array_dimensions: Option<Vec<u32>> = |value| value.array_dimensions.clone(),
        max_string_length: u32 = |value| value.max_string_length,
        is_optional: bool = |value| value.is_optional,
    }
}

ua_struct! {
    /// A decoded structure schema, not a decoder for arbitrary instances.
    pub struct StructureDefinition ord(super::ordering::structure_definition) {
        default_encoding_id: NodeId = |value| NodeId(value.default_encoding_id.clone()),
        base_data_type: NodeId = |value| NodeId(value.base_data_type.clone()),
        structure_type: StructureType = |value| StructureType(value.structure_type),

        fields: Vec<StructureField> = |value| value.fields
            .iter()
            .flatten()
            .cloned()
            .map(StructureField)
            .collect(),
    }
}

ua_struct! {
    /// One named integer field in an EnumDefinition.
    pub struct EnumField ord(super::ordering::enum_field) {
        name: String = |value| value.name.as_ref().to_owned(),
        value: i64 = |value| value.value,
        display_name: LocalizedText = |value| LocalizedText(value.display_name.clone()),
        description: LocalizedText = |value| LocalizedText(value.description.clone()),
    }
}

ua_struct! {
    /// A decoded enumeration schema with its field metadata.
    pub struct EnumDefinition ord(super::ordering::enum_definition) {
        fields: Vec<EnumField> = |value| value.fields
            .iter()
            .flatten()
            .cloned()
            .map(EnumField)
            .collect(),
    }
}

ua_struct! {
    /// A starting NodeId and relative route; Node paths start at RootFolder.
    pub struct BrowsePath ord(super::ordering::browse_path) {
        starting_node: NodeId = |value| NodeId(value.starting_node.clone()),
        relative_path: RelativePath = |value| RelativePath(value.relative_path.clone()),
    }
}

ua_struct! {
    #[derive(Copy, Eq)]
    /// An OPC UA status retaining quality and all original status bits.
    ///
    /// Construct a status from all of its native bits.
    ///
    /// Args:
    ///     value (int): Unsigned 32-bit status code.
    ///
    /// Returns:
    ///     StatusCode: A status retaining quality and informational bits.
    ///
    /// Examples:
    ///     >>> StatusCode(0).is_good
    ///     True
    pub struct StatusCode ord(super::ordering::status_code) {
        code: u32 = |value| value.bits(),
        is_good: bool = |value| value.is_good(),
        is_bad: bool = |value| value.is_bad(),
        is_uncertain: bool = |value| value.is_uncertain(),
    }
}

impl std::hash::Hash for StatusCode {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.0.hash(state);
    }
}

#[pymethods]
impl StatusCode {
    pub fn __hash__(&self) -> u64 {
        let mut h = DefaultHasher::new();
        h.write_u32(self.0.bits());
        h.finish()
    }
}

#[pymethods]
impl StatusCode {
    #[new]
    fn new(value: u32) -> Self {
        Self(value.into())
    }

    fn __int__(&self) -> u32 {
        self.0.bits()
    }

    /// Return the native status subcode name.
    fn name(&self) -> &'static str {
        self.0.sub_code().name()
    }

    fn __str__(&self) -> String {
        self.0.to_string()
    }

    fn __repr__(&self) -> String {
        format!("StatusCode(0x{:08x})", self.0.bits())
    }
}

ua_struct! {
    #[derive(Eq)]
    /// A namespace index and numeric, string, UUID, or byte-string identifier.
    ///
    /// Parse a native textual NodeId.
    ///
    /// Args:
    ///     text (str): Numeric, string, Guid, or byte-string NodeId syntax.
    ///
    /// Returns:
    ///     NodeId: Parsed identifier.
    ///
    /// Examples:
    ///     >>> NodeId("ns=2;s=Temperature").identifier
    ///     'Temperature'
    pub struct NodeId ord(super::ordering::node_id) {
        namespace_index: u16 = |value| value.namespace,
    }
}

impl std::hash::Hash for NodeId {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.0.hash(state);
    }
}

#[pymethods]
impl NodeId {
    #[new]
    fn new(text: &str) -> PyResult<Self> {
        ua::NodeId::from_str(text)
            .map(Self)
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    /// Parse a textual NodeId, equivalent to the constructor.
    ///
    /// Args:
    ///     id_str (str): Native NodeId syntax.
    ///
    /// Returns:
    ///     NodeId: Parsed identifier.
    #[staticmethod]
    fn from_string(id_str: &str) -> PyResult<Self> {
        Self::new(id_str)
    }

    pub fn __hash__(&self) -> u64 {
        let mut h = DefaultHasher::new();
        self.hash(&mut h);
        h.finish()
    }

    /// int | str | uuid.UUID | bytes: The identifier in its corresponding Python type.
    #[getter]
    pub fn identifier(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.0.identifier {
            ua::Identifier::Numeric(value) => value.into_py_any(py),
            ua::Identifier::String(value) => value.value().into_py_any(py),
            ua::Identifier::Guid(value) => uuid::Uuid::from(value.clone()).into_py_any(py),

            ua::Identifier::ByteString(value) => {
                Ok(PyBytes::new(py, value.as_ref()).into_any().unbind())
            }
        }
    }

    fn __str__(&self) -> String {
        self.0.to_string()
    }

    fn __repr__(&self) -> String {
        format!("NodeId({:?})", self.0.to_string())
    }
}

ua_struct! {
    #[derive(Eq)]
    /// A browse name qualified by its namespace index.
    ///
    /// Construct a qualified browse name.
    ///
    /// Args:
    ///     namespace_index (int): Unsigned 16-bit namespace index.
    ///     name (str): Browse name within that namespace.
    ///
    /// Returns:
    ///     QualifiedName: The qualified name.
    ///
    /// Examples:
    ///     >>> QualifiedName(12, "Temperature").namespace_index
    ///     12
    pub struct QualifiedName ord(super::ordering::qualified_name) {
        namespace_index: u16 = |value| value.namespace_index,
        name: String = |value| value.name.as_ref().to_owned(),
    }
}

impl std::hash::Hash for QualifiedName {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.0.hash(state);
    }
}

#[pymethods]
impl QualifiedName {
    #[new]
    fn new(namespace_index: u16, name: String) -> Self {
        Self(ua::QualifiedName::new(namespace_index, name))
    }

    pub fn __hash__(&self) -> u64 {
        let mut h = DefaultHasher::new();
        self.hash(&mut h);
        h.finish()
    }

    pub fn __str__(&self) -> String {
        self.0.to_string()
    }

    fn __repr__(&self) -> String {
        format!(
            "QualifiedName({}, {:?})",
            self.0.namespace_index,
            self.0.name.value()
        )
    }
}

ua_struct! {
    /// Text with an optional locale identifier.
    ///
    /// Construct text with optional locale metadata.
    ///
    /// Args:
    ///     text (str | None): Text content. Defaults to ``None``.
    ///     locale (str | None): Locale identifier. Defaults to ``None``.
    ///
    /// Returns:
    ///     LocalizedText: Text whose absent content reads as an empty string.
    ///
    /// Examples:
    ///     >>> LocalizedText("Temperature", "en-US").text
    ///     'Temperature'
    pub struct LocalizedText ord(super::ordering::localized_text) {
        text: String = |value| value.text.as_ref().to_owned(),
        locale: Option<String> = |value| value.locale
            .value()
            .as_ref()
            .filter(|locale| !locale.is_empty())
            .cloned(),
    }
}

#[pymethods]
impl LocalizedText {
    #[new]
    #[pyo3(signature = (text=None, locale=None))]
    fn new(text: Option<String>, locale: Option<String>) -> Self {
        Self(ua::LocalizedText {
            text: text.into(),
            locale: locale.into(),
        })
    }
}

ua_struct! {
    #[derive(Eq)]
    /// A NodeId with optional namespace URI and server index.
    pub struct ExpandedNodeId ord(super::ordering::expanded_node_id) {
        node_id: NodeId = |value| NodeId(value.node_id.clone()),
        server_index: u32 = |value| value.server_index,

        namespace_uri: Option<String> = |value| value.namespace_uri
            .value()
            .as_ref()
            .filter(|uri| !uri.is_empty())
            .cloned(),
    }
}

ua_struct! {
    /// One directed reference step and target qualified name.
    ///
    /// Construct a directed browse-path step.
    ///
    /// Args:
    ///     target_name (QualifiedName): Target browse name.
    ///     reference_type_id (NodeId): Reference type to follow.
    ///     is_inverse (bool): Follow references in the inverse direction.
    ///     include_subtypes (bool): Include reference-type subtypes.
    ///
    /// Returns:
    ///     RelativePathElement: A path step.
    ///
    /// Examples:
    ///     >>> step = RelativePathElement(QualifiedName(0, "Objects"), NodeId("i=33"), False, True)
    ///     >>> step.include_subtypes
    ///     True
    pub struct RelativePathElement ord(super::ordering::relative_path_element) {
        is_inverse: bool = |value| value.is_inverse,
        include_subtypes: bool = |value| value.include_subtypes,
        reference_type_id: NodeId = |value| NodeId(value.reference_type_id.clone()),
        target_name: QualifiedName = |value| QualifiedName(value.target_name.clone()),
    }
}

#[pymethods]
impl RelativePathElement {
    #[new]
    pub fn new(
        target_name: QualifiedName,
        reference_type_id: NodeId,
        is_inverse: bool,
        include_subtypes: bool,
    ) -> Self {
        Self(ua::RelativePathElement {
            target_name: target_name.0,
            reference_type_id: reference_type_id.0,
            is_inverse,
            include_subtypes,
        })
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RelativePathElement(target_name={:?}, reference_type_id={:?}, is_inverse={:?}, \
             include_subtypes={:?})",
            self.0.target_name,
            self.0.reference_type_id,
            self.0.is_inverse,
            self.0.include_subtypes
        )
    }
}

ua_struct! {
    /// An ordered native browse route, parsed or constructed from qualified names.
    ///
    /// Parse a browse path using the native parser.
    ///
    /// The native parser has a known multi-digit namespace-index limitation.
    /// Use ``from_qualified_names()`` for structured hierarchical paths.
    ///
    /// Args:
    ///     text (str): Native relative-path syntax.
    ///
    /// Returns:
    ///     RelativePath: Parsed ordered path elements.
    ///
    /// Examples:
    ///     >>> path = RelativePath("/Objects/Server")
    ///     >>> len(path.elements)
    ///     2
    pub struct RelativePath ord(super::ordering::relative_path) {
        elements: Vec<RelativePathElement> = |value| {
            value
                .elements
                .as_deref()
                .unwrap_or_default()
                .iter()
                .cloned()
                .map(RelativePathElement)
                .collect()
        },
    }
}

#[pymethods]
impl RelativePath {
    #[new]
    fn new(text: &str) -> PyResult<Self> {
        ua::RelativePath::try_from(text)
            .map(Self)
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    /// Parse a browse path, equivalent to the constructor.
    ///
    /// Args:
    ///     path_str (str): Native relative-path syntax.
    ///
    /// Returns:
    ///     RelativePath: Parsed ordered path elements.
    #[staticmethod]
    fn parse(path_str: &str) -> PyResult<Self> {
        Self::new(path_str)
    }

    fn __hash__(&self) -> u64 {
        let mut h = DefaultHasher::new();

        for element in self.0.elements.iter().flatten() {
            element.reference_type_id.hash(&mut h);
            element.target_name.hash(&mut h);
            element.is_inverse.hash(&mut h);
            element.include_subtypes.hash(&mut h);
        }

        h.finish()
    }

    /// Construct forward hierarchical steps from qualified browse names.
    ///
    /// Args:
    ///     names (list[QualifiedName]): Names in path order; each step includes
    ///         hierarchical reference subtypes.
    ///
    /// Returns:
    ///     RelativePath: The structured native path, without string parsing.
    #[staticmethod]
    fn from_qualified_names(names: Vec<QualifiedName>) -> Self {
        let names = names.into_iter().map(|name| name.0).collect::<Vec<_>>();
        Self(ua::RelativePath::from(names.as_slice()))
    }

    pub fn __repr__(&self) -> String {
        format!("RelativePath('{:?}')", String::from(&self.0))
    }

    fn __str__(&self) -> String {
        String::from(&self.0)
    }
}

pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<AttributeId>()?;
    module.add_class::<MessageSecurityMode>()?;
    module.add_class::<ApplicationType>()?;
    module.add_class::<UserTokenType>()?;
    module.add_class::<ApplicationDescription>()?;
    module.add_class::<UserTokenPolicy>()?;
    module.add_class::<EndpointDescription>()?;
    module.add_class::<NodeClass>()?;
    module.add_class::<AttributeWriteMask>()?;
    module.add_class::<AccessLevelType>()?;
    module.add_class::<AccessLevelExType>()?;
    module.add_class::<AccessRestrictionType>()?;
    module.add_class::<EventNotifierType>()?;
    module.add_class::<PermissionType>()?;
    module.add_class::<StructureType>()?;
    module.add_class::<RolePermissionType>()?;
    module.add_class::<StructureField>()?;
    module.add_class::<StructureDefinition>()?;
    module.add_class::<EnumField>()?;
    module.add_class::<EnumDefinition>()?;
    module.add_class::<BrowsePath>()?;
    module.add_class::<StatusCode>()?;
    module.add_class::<NodeId>()?;
    module.add_class::<QualifiedName>()?;
    module.add_class::<LocalizedText>()?;
    module.add_class::<ExpandedNodeId>()?;
    module.add_class::<RelativePath>()?;
    module.add_class::<RelativePathElement>()?;

    Ok(())
}

impl std::hash::Hash for ExpandedNodeId {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.0.hash(state);
    }
}

#[pymethods]
impl ExpandedNodeId {
    fn __hash__(&self) -> u64 {
        let mut h = DefaultHasher::new();
        self.hash(&mut h);
        h.finish()
    }

    fn __str__(&self) -> String {
        self.0.to_string()
    }
}

#[pymethods]
impl RelativePathElement {
    fn __hash__(&self) -> u64 {
        let mut h = DefaultHasher::new();
        self.0.reference_type_id.hash(&mut h);
        self.0.target_name.hash(&mut h);
        self.0.is_inverse.hash(&mut h);
        self.0.include_subtypes.hash(&mut h);
        h.finish()
    }
}

ua_enum!(MessageSecurityMode(ua::MessageSecurityMode, i32) {
    from(value) => ua::MessageSecurityMode::try_from(value).map_err(crate::error::native);
    value(inner) => inner as i32;

    None,
    Sign,
    SignAndEncrypt,
});

ua_enum!(ApplicationType(ua::ApplicationType, i32) {
    from(value) => ua::ApplicationType::try_from(value).map_err(crate::error::native);
    value(inner) => inner as i32;

    Server,
    Client,
    ClientAndServer,
    DiscoveryServer,
});

ua_enum!(UserTokenType(ua::UserTokenType, i32) {
    from(value) => ua::UserTokenType::try_from(value).map_err(crate::error::native);
    value(inner) => inner as i32;

    Anonymous,
    UserName,
    Certificate,
    IssuedToken,
});

ua_struct! {
    /// Server application identity and discovery metadata.
    pub struct ApplicationDescription {
        application_uri: String = |value| value.application_uri.as_ref().to_owned(),
        product_uri: String = |value| value.product_uri.as_ref().to_owned(),
        application_name: LocalizedText = |value| LocalizedText(value.application_name.clone()),
        application_type: ApplicationType = |value| ApplicationType(value.application_type),
        gateway_server_uri: String = |value| value.gateway_server_uri.as_ref().to_owned(),
        discovery_profile_uri: String = |value| value.discovery_profile_uri.as_ref().to_owned(),
        discovery_urls: Vec<String> = |value| value.discovery_urls
            .iter()
            .flatten()
            .map(|url| url.as_ref().to_owned())
            .collect(),
    }
}

ua_struct! {
    /// A user-token policy advertised by an endpoint.
    pub struct UserTokenPolicy {
        policy_id: String = |value| value.policy_id.as_ref().to_owned(),
        token_type: UserTokenType = |value| UserTokenType(value.token_type),
        issued_token_type: String = |value| value.issued_token_type.as_ref().to_owned(),
        issuer_endpoint_url: String = |value| value.issuer_endpoint_url.as_ref().to_owned(),
        security_policy_uri: String = |value| value.security_policy_uri.as_ref().to_owned(),
    }
}

ua_struct! {
    /// Endpoint security, user-token policies, and flattened application metadata.
    pub struct EndpointDescription ord(super::ordering::endpoint_description) {
        endpoint_url: String = |value| value.endpoint_url.as_ref().to_owned(),
        server: ApplicationDescription = |value| ApplicationDescription(value.server.clone()),
        security_mode: MessageSecurityMode = |value| MessageSecurityMode(value.security_mode),
        security_policy: String = |value| value.security_policy_uri.as_ref().to_owned(),
        security_level: u8 = |value| value.security_level,
        transport_profile_uri: String = |value| value.transport_profile_uri.as_ref().to_owned(),
        application_uri: String = |value| value.server.application_uri.as_ref().to_owned(),
        product_uri: String = |value| value.server.product_uri.as_ref().to_owned(),
        gateway_server_uri: String = |value| value.server.gateway_server_uri.as_ref().to_owned(),
        application_name: String = |value| value.server.application_name.text.as_ref().to_owned(),
        application_type: ApplicationType = |value| ApplicationType(value.server.application_type),

        user_identity_tokens: Vec<UserTokenPolicy> = |value| value.user_identity_tokens
            .iter()
            .flatten()
            .cloned()
            .map(UserTokenPolicy)
            .collect(),

        discovery_profile_uri: String = |value| value.server.discovery_profile_uri
            .as_ref()
            .to_owned(),

        discovery_urls: Vec<String> = |value| value.server.discovery_urls
            .iter()
            .flatten()
            .map(|url| url.as_ref().to_owned())
            .collect(),
    }
}

#[pymethods]
impl EndpointDescription {
    /// bytes: The server certificate advertised by this endpoint.
    #[getter]
    fn server_certificate<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.0.server_certificate.as_ref())
    }

    fn __hash__(&self) -> u64 {
        let mut h = DefaultHasher::new();

        self.0.endpoint_url.hash(&mut h);
        self.0.security_policy_uri.hash(&mut h);
        self.0.transport_profile_uri.hash(&mut h);

        self.0.server_certificate.hash(&mut h);
        (self.0.security_mode as i32).hash(&mut h);
        self.0.security_level.hash(&mut h);

        let server = &self.0.server;
        server.application_uri.hash(&mut h);
        server.product_uri.hash(&mut h);
        server.application_name.locale.hash(&mut h);
        server.application_name.text.hash(&mut h);
        (server.application_type as i32).hash(&mut h);

        server.gateway_server_uri.hash(&mut h);
        server.discovery_profile_uri.hash(&mut h);
        server
            .discovery_urls
            .as_deref()
            .unwrap_or_default()
            .hash(&mut h);

        for token in self.0.user_identity_tokens.iter().flatten() {
            token.policy_id.hash(&mut h);
            (token.token_type as i32).hash(&mut h);
            token.issued_token_type.hash(&mut h);
            token.issuer_endpoint_url.hash(&mut h);
            token.security_policy_uri.hash(&mut h);
        }

        h.finish()
    }
}

#[pymethods]
impl LocalizedText {
    fn __hash__(&self) -> u64 {
        let mut h = DefaultHasher::new();
        self.0.locale.hash(&mut h);
        self.0.text.hash(&mut h);
        h.finish()
    }
}
