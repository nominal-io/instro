use std::cmp::Ordering;
use std::hash::Hash;
use std::hash::Hasher;
use std::str::FromStr;

use instro_opcua_macros::ua_enum;
use instro_opcua_macros::ua_struct;
use opcua::types as ua;
use opcua::types::UaNullable;
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyAny;
use pyo3::types::PyBytes;

fn hash_array<'a, T, K: Hash>(
    values: Option<&'a [T]>,
    key: impl Fn(&'a T) -> K,
    state: &mut impl Hasher,
) {
    values.map(<[T]>::len).hash(state);

    for value in values.into_iter().flatten() {
        key(value).hash(state);
    }
}

#[ua_enum(repr = u32)]
#[derive(PartialEq, Eq, Hash)]
pub(crate) enum AttributeId {
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
}

#[ua_enum(repr = i32, wire = scalar, py_traits = (hash))]
#[derive(PartialEq, Eq)]
pub(crate) enum NodeClass {
    Unspecified,
    Object,
    Variable,
    Method,
    ObjectType,
    VariableType,
    ReferenceType,
    DataType,
    View,
}

#[ua_enum(repr = i32, py_traits = (hash))]
#[derive(PartialEq, Eq)]
pub(crate) enum StructureType {
    Structure,
    StructureWithOptionalFields,
    Union,
    StructureWithSubtypedValues,
    UnionWithSubtypedValues,
}

#[ua_enum(flags, repr = u32, wire = scalar, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) enum AttributeWriteMask {
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
}

#[ua_enum(flags, repr = u8, wire = scalar, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) enum AccessLevelType {
    CurrentRead,
    CurrentWrite,
    HistoryRead,
    HistoryWrite,
    SemanticChange,
    StatusWrite,
    TimestampWrite,
}

#[ua_enum(flags, repr = u32, wire = scalar, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) enum AccessLevelExType {
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
}

#[ua_enum(flags, repr = u16, wire = scalar, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) enum AccessRestrictionType {
    SigningRequired,
    EncryptionRequired,
    SessionRequired,
    ApplyRestrictionsToBrowse,
}

#[ua_enum(flags, repr = u8, wire = scalar, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) enum EventNotifierType {
    SubscribeToEvents,
    HistoryRead,
    HistoryWrite,
}

#[ua_enum(flags, repr = i32, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) enum PermissionType {
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
}

/// A role identifier and its PermissionType flags.
#[ua_struct(wire = structure, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) struct RolePermissionType {
    role_id: NodeId,
    permissions: PermissionType,
}

/// One field in a StructureDefinition, including type and array metadata.
#[ua_struct(wire = structure, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) struct StructureField {
    name: String,
    description: LocalizedText,
    data_type: NodeId,
    value_rank: i32,
    array_dimensions: Option<Vec<u32>>,
    max_string_length: u32,
    is_optional: bool,
}

fn structure_field(value: &ua::StructureField) -> impl Hash + '_ {
    (
        value.name.value(),
        localized_text(&value.description),
        node_id(&value.data_type),
        value.value_rank,
        &value.array_dimensions,
        value.max_string_length,
        value.is_optional,
    )
}

impl Hash for StructureField {
    fn hash<H: Hasher>(&self, state: &mut H) {
        structure_field(&self.0).hash(state);
    }
}

/// A decoded structure schema, not a decoder for arbitrary instances.
#[ua_struct(wire = structure, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) struct StructureDefinition {
    default_encoding_id: NodeId,
    base_data_type: NodeId,
    structure_type: StructureType,
    #[ua(get = self.0.fields.iter().flatten().cloned().map(StructureField::from).collect())]
    fields: Vec<StructureField>,
}

impl Hash for StructureDefinition {
    fn hash<H: Hasher>(&self, state: &mut H) {
        node_id(&self.0.default_encoding_id).hash(state);
        node_id(&self.0.base_data_type).hash(state);
        (self.0.structure_type as u32).hash(state);
        hash_array(self.0.fields.as_deref(), structure_field, state);
    }
}

/// One named integer field in an EnumDefinition.
#[ua_struct(wire = structure, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) struct EnumField {
    name: String,
    value: i64,
    display_name: LocalizedText,
    description: LocalizedText,
}

fn enum_field(value: &ua::EnumField) -> impl Hash + '_ {
    (
        value.value,
        localized_text(&value.display_name),
        localized_text(&value.description),
        value.name.value(),
    )
}

impl Hash for EnumField {
    fn hash<H: Hasher>(&self, state: &mut H) {
        enum_field(&self.0).hash(state);
    }
}

/// A decoded enumeration schema with its field metadata.
#[ua_struct(wire = structure, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) struct EnumDefinition {
    #[ua(get = self.0.fields.iter().flatten().cloned().map(EnumField::from).collect())]
    fields: Vec<EnumField>,
}

impl Hash for EnumDefinition {
    fn hash<H: Hasher>(&self, state: &mut H) {
        hash_array(self.0.fields.as_deref(), enum_field, state);
    }
}

/// A starting NodeId and relative route; Node paths start at RootFolder.
#[ua_struct(py_traits = (ord, hash))]
#[derive(PartialEq)]
pub struct BrowsePath {
    starting_node: NodeId,
    relative_path: RelativePath,
}

impl PartialOrd for BrowsePath {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        let start = node_id(&self.0.starting_node).cmp(&node_id(&other.0.starting_node));
        Some(start.then_with(|| compare_paths(&self.0.relative_path, &other.0.relative_path)))
    }
}

impl Hash for BrowsePath {
    fn hash<H: Hasher>(&self, state: &mut H) {
        node_id(&self.0.starting_node).hash(state);
        hash_array(
            self.0.relative_path.elements.as_deref(),
            relative_path_element,
            state,
        );
    }
}

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
#[ua_struct(wire = scalar)]
#[derive(Copy, PartialEq, Eq, Hash)]
pub(crate) struct StatusCode {}

#[pymethods]
impl StatusCode {
    #[getter]
    fn code(&self) -> u32 {
        self.0.bits()
    }

    #[getter]
    fn is_good(&self) -> bool {
        self.0.is_good()
    }

    #[getter]
    fn is_bad(&self) -> bool {
        self.0.is_bad()
    }

    #[getter]
    fn is_uncertain(&self) -> bool {
        self.0.is_uncertain()
    }

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
#[ua_struct(wire = scalar, py_traits = (ord))]
#[derive(PartialEq, Eq, Hash)]
pub(crate) struct NodeId {}

#[derive(PartialEq, Eq, PartialOrd, Ord, Hash)]
enum IdentifierKey<'a> {
    Numeric(u32),
    String(Option<&'a str>),
    Guid(uuid::Uuid),
    ByteString(Option<&'a [u8]>),
}

fn node_id(value: &ua::NodeId) -> impl Ord + Hash + '_ {
    let identifier = match &value.identifier {
        ua::Identifier::Numeric(number) => IdentifierKey::Numeric(*number),
        ua::Identifier::String(text) => IdentifierKey::String(text.value().as_deref()),
        ua::Identifier::Guid(guid) => IdentifierKey::Guid(uuid::Uuid::from(guid.clone())),
        ua::Identifier::ByteString(bytes) => IdentifierKey::ByteString(bytes.value.as_deref()),
    };

    (value.namespace, identifier)
}

impl PartialOrd for NodeId {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(node_id(&self.0).cmp(&node_id(&other.0)))
    }
}

#[pymethods]
impl NodeId {
    #[getter]
    fn namespace_index(&self) -> u16 {
        self.0.namespace
    }

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
#[ua_struct(wire = scalar, py_traits = (ord))]
#[derive(PartialEq, Eq, Hash)]
pub(crate) struct QualifiedName {
    namespace_index: u16,
    name: String,
}

fn qualified_name(value: &ua::QualifiedName) -> impl Ord + Hash + '_ {
    (value.namespace_index, value.name.value())
}

impl PartialOrd for QualifiedName {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(qualified_name(&self.0).cmp(&qualified_name(&other.0)))
    }
}

#[pymethods]
impl QualifiedName {
    #[new]
    fn new(namespace_index: u16, name: String) -> Self {
        Self(ua::QualifiedName::new(namespace_index, name))
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

/// Text with an optional locale identifier.
///
/// Construct text with optional locale metadata.
///
/// Args:
///     text (str | None): Text content. Defaults to ``None``.
///     locale (str | None): Locale identifier. Defaults to ``None``.
///
/// Returns:
///     LocalizedText: Absent text and locale fields read as ``None``.
///         Present empty strings read as ``""``.
///
/// Examples:
///     >>> LocalizedText("Temperature", "en-US").text
///     'Temperature'
#[ua_struct(wire = scalar, py_traits = (ord, hash))]
#[derive(PartialEq)]
pub(crate) struct LocalizedText {
    #[ua(get = self.0.text.value().clone())]
    text: Option<String>,
    #[ua(get = self.0.locale.value().clone())]
    locale: Option<String>,
}

fn localized_text(value: &ua::LocalizedText) -> impl Ord + Hash + '_ {
    (value.locale.value(), value.text.value())
}

impl PartialOrd for LocalizedText {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(localized_text(&self.0).cmp(&localized_text(&other.0)))
    }
}

impl Hash for LocalizedText {
    fn hash<H: Hasher>(&self, state: &mut H) {
        localized_text(&self.0).hash(state);
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

/// XML text carried as an OPC UA XmlElement.
///
/// Args:
///     text (str): XML text to retain.
///
/// Examples:
///     >>> str(XmlElement("<mode>on</mode>"))
///     '<mode>on</mode>'
#[ua_struct(wire = scalar, py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) struct XmlElement {}

#[pymethods]
impl XmlElement {
    #[new]
    fn new(text: String) -> Self {
        Self(text.into())
    }

    fn __str__(&self) -> String {
        self.0.to_string()
    }
}

/// A NodeId with optional namespace URI and server index.
#[ua_struct(wire = scalar, py_traits = (ord))]
#[derive(PartialEq, Eq, Hash)]
pub(crate) struct ExpandedNodeId {
    node_id: NodeId,
    server_index: u32,

    /// str | None: The namespace URI. An absent URI is ``None`` and a present empty URI is ``""``.
    #[ua(get = self.0.namespace_uri.value().clone())]
    namespace_uri: Option<String>,
}

impl PartialOrd for ExpandedNodeId {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        let left = (self.0.server_index, self.0.namespace_uri.value());
        let right = (other.0.server_index, other.0.namespace_uri.value());

        Some(
            left.cmp(&right)
                .then_with(|| node_id(&self.0.node_id).cmp(&node_id(&other.0.node_id))),
        )
    }
}

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
#[ua_struct(py_traits = (ord, hash))]
#[derive(PartialEq)]
pub(crate) struct RelativePathElement {
    is_inverse: bool,
    include_subtypes: bool,
    reference_type_id: NodeId,
    target_name: QualifiedName,
}

fn relative_path_element(value: &ua::RelativePathElement) -> impl Ord + Hash + '_ {
    (
        node_id(&value.reference_type_id),
        value.is_inverse,
        value.include_subtypes,
        qualified_name(&value.target_name),
    )
}

impl PartialOrd for RelativePathElement {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(relative_path_element(&self.0).cmp(&relative_path_element(&other.0)))
    }
}

fn path_element_fields(element: &ua::RelativePathElement) -> impl Hash + '_ {
    (
        &element.reference_type_id,
        element.is_inverse,
        element.include_subtypes,
        &element.target_name,
    )
}

impl Hash for RelativePathElement {
    fn hash<H: Hasher>(&self, state: &mut H) {
        path_element_fields(&self.0).hash(state);
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
#[ua_struct(py_traits = (ord, hash))]
#[derive(PartialEq)]
pub(crate) struct RelativePath {
    #[ua(get = self.0.elements.iter().flatten().cloned().map(RelativePathElement::from).collect())]
    elements: Vec<RelativePathElement>,
}

fn compare_paths(left: &ua::RelativePath, right: &ua::RelativePath) -> Ordering {
    match (&left.elements, &right.elements) {
        (Some(left), Some(right)) => left
            .iter()
            .map(relative_path_element)
            .cmp(right.iter().map(relative_path_element)),
        (left, right) => left.is_some().cmp(&right.is_some()),
    }
}

impl PartialOrd for RelativePath {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(compare_paths(&self.0, &other.0))
    }
}

impl Hash for RelativePath {
    fn hash<H: Hasher>(&self, state: &mut H) {
        hash_array(self.0.elements.as_deref(), path_element_fields, state);
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
    module.add_class::<XmlElement>()?;
    module.add_class::<ExpandedNodeId>()?;
    module.add_class::<RelativePath>()?;
    module.add_class::<RelativePathElement>()?;

    Ok(())
}

#[pymethods]
impl ExpandedNodeId {
    fn __str__(&self) -> String {
        self.0.to_string()
    }
}

impl Hash for RolePermissionType {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.role_id.hash(state);
        self.0.permissions.bits().hash(state);
    }
}

impl Hash for XmlElement {
    fn hash<H: Hasher>(&self, state: &mut H) {
        // The native type exposes its nullness and text but keeps its UAString private.
        let text = (!self.0.is_ua_null()).then(|| self.0.to_string());
        text.hash(state);
    }
}

#[ua_enum(repr = i32, py_traits = (hash))]
#[derive(PartialEq, Eq)]
pub(crate) enum MessageSecurityMode {
    None,
    Sign,
    SignAndEncrypt,
}

#[ua_enum(repr = i32, py_traits = (hash))]
#[derive(PartialEq, Eq)]
pub(crate) enum ApplicationType {
    Server,
    Client,
    ClientAndServer,
    DiscoveryServer,
}

#[ua_enum(repr = i32, py_traits = (hash))]
#[derive(PartialEq, Eq)]
pub(crate) enum UserTokenType {
    Anonymous,
    UserName,
    Certificate,
    IssuedToken,
}

/// Server application identity and discovery metadata.
#[ua_struct(py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) struct ApplicationDescription {
    application_uri: String,
    product_uri: String,
    application_name: LocalizedText,
    application_type: ApplicationType,
    gateway_server_uri: String,
    discovery_profile_uri: String,
    #[ua(get = discovery_urls(&self.0))]
    discovery_urls: Vec<String>,
}

fn discovery_urls(server: &ua::ApplicationDescription) -> Vec<String> {
    server
        .discovery_urls
        .iter()
        .flatten()
        .map(|url| url.as_ref().to_owned())
        .collect()
}

fn application_description(value: &ua::ApplicationDescription) -> impl Hash + '_ {
    (
        value.application_uri.value(),
        value.product_uri.value(),
        localized_text(&value.application_name),
        value.application_type as i32,
        value.gateway_server_uri.value(),
        value.discovery_profile_uri.value(),
        &value.discovery_urls,
    )
}

impl Hash for ApplicationDescription {
    fn hash<H: Hasher>(&self, state: &mut H) {
        application_description(&self.0).hash(state);
    }
}

/// A user-token policy advertised by an endpoint.
#[ua_struct(py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) struct UserTokenPolicy {
    policy_id: String,
    token_type: UserTokenType,
    issued_token_type: String,
    issuer_endpoint_url: String,
    security_policy_uri: String,
}

fn user_token_policy(value: &ua::UserTokenPolicy) -> impl Hash + '_ {
    (
        value.policy_id.value(),
        value.token_type as i32,
        value.issued_token_type.value(),
        value.issuer_endpoint_url.value(),
        value.security_policy_uri.value(),
    )
}

impl Hash for UserTokenPolicy {
    fn hash<H: Hasher>(&self, state: &mut H) {
        user_token_policy(&self.0).hash(state);
    }
}

/// Endpoint security, user-token policies, and flattened application metadata.
#[ua_struct(py_traits = (hash))]
#[derive(PartialEq)]
pub(crate) struct EndpointDescription {
    endpoint_url: String,
    server: ApplicationDescription,
    security_mode: MessageSecurityMode,
    security_level: u8,
    transport_profile_uri: String,
    #[ua(get = self.0.user_identity_tokens.iter().flatten().cloned().map(UserTokenPolicy::from).collect())]
    user_identity_tokens: Vec<UserTokenPolicy>,
    #[ua(get = self.0.security_policy_uri.as_ref().to_owned())]
    security_policy: String,
    #[ua(get = self.0.server.application_uri.as_ref().to_owned())]
    application_uri: String,
    #[ua(get = self.0.server.product_uri.as_ref().to_owned())]
    product_uri: String,
    #[ua(get = self.0.server.gateway_server_uri.as_ref().to_owned())]
    gateway_server_uri: String,
    #[ua(get = self.0.server.application_name.text.as_ref().to_owned())]
    application_name: String,
    #[ua(get = self.0.server.application_type.into())]
    application_type: ApplicationType,
    #[ua(get = self.0.server.discovery_profile_uri.as_ref().to_owned())]
    discovery_profile_uri: String,
    #[ua(get = discovery_urls(&self.0.server))]
    discovery_urls: Vec<String>,
}

impl Hash for EndpointDescription {
    fn hash<H: Hasher>(&self, state: &mut H) {
        let endpoint = &self.0;
        endpoint.endpoint_url.hash(state);
        endpoint.security_policy_uri.hash(state);
        endpoint.transport_profile_uri.hash(state);
        endpoint.server_certificate.hash(state);
        (endpoint.security_mode as i32).hash(state);
        endpoint.security_level.hash(state);
        application_description(&endpoint.server).hash(state);

        let tokens = endpoint.user_identity_tokens.as_deref();
        hash_array(tokens, user_token_policy, state);
    }
}

#[pymethods]
impl EndpointDescription {
    /// bytes: The server certificate advertised by this endpoint.
    #[getter]
    fn server_certificate<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.0.server_certificate.as_ref())
    }
}

#[cfg(test)]
mod identity_tests {
    use super::*;

    #[test]
    fn native_fields_precede_rendering_and_little_endian_guid_bytes() {
        use Ordering::Greater;
        use Ordering::Less;

        let cases = [
            ("ns=1;i=999", "ns=2;i=1", Less),
            ("ns=1;i=999", "ns=1;s=a", Less),
            ("ns=1;i=10", "ns=1;i=2", Greater),
            ("ns=1;s=z", "ns=1;s=aa", Greater),
            ("b=/w==", "b=AAE=", Greater),
            (
                "g=00000001-0000-0000-0000-000000000000",
                "g=00000100-0000-0000-0000-000000000000",
                Less,
            ),
        ];

        for (left, right, expected) in cases {
            let left = NodeId::new(left).expect("left fixture");
            let right = NodeId::new(right).expect("right fixture");
            assert_eq!(left.partial_cmp(&right), Some(expected));
        }
    }

    #[test]
    fn expanded_node_id_exposes_present_empty_namespace_uri() {
        let absent = ExpandedNodeId(ua::ExpandedNodeId::default());
        let empty = ExpandedNodeId(ua::ExpandedNodeId {
            namespace_uri: ua::UAString::from(""),
            ..absent.0.clone()
        });

        assert_eq!(absent.namespace_uri(), None);
        assert_eq!(empty.namespace_uri().as_deref(), Some(""));
        assert!(absent < empty);
    }

    #[track_caller]
    fn assert_absent_differs_from_empty<T, W: PartialEq>(wrap: impl Fn(Option<Vec<T>>) -> W) {
        let absent = wrap(None);
        let empty = wrap(Some(vec![]));
        assert!(absent != empty);
    }

    #[test]
    fn nullable_array_fields_distinguish_absent_and_empty() {
        assert_absent_differs_from_empty(|fields| EnumDefinition(ua::EnumDefinition { fields }));
        assert_absent_differs_from_empty(|array_dimensions| {
            StructureField(ua::StructureField {
                array_dimensions,
                ..Default::default()
            })
        });

        assert_absent_differs_from_empty(|fields| {
            StructureDefinition(ua::StructureDefinition {
                fields,
                ..Default::default()
            })
        });

        assert_absent_differs_from_empty(|user_identity_tokens| {
            EndpointDescription(ua::EndpointDescription {
                user_identity_tokens,
                ..Default::default()
            })
        });

        assert_absent_differs_from_empty(|discovery_urls| {
            let server = ua::ApplicationDescription {
                discovery_urls,
                ..Default::default()
            };

            EndpointDescription(ua::EndpointDescription {
                server,
                ..Default::default()
            })
        });
    }

    #[test]
    fn absent_relative_path_sorts_before_empty_path() {
        let absent = RelativePath(ua::RelativePath { elements: None });
        let empty = RelativePath(ua::RelativePath {
            elements: Some(Vec::new()),
        });
        assert!(absent != empty);
        assert!(absent < empty);
    }
}
