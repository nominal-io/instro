use std::str::FromStr;

use opcua::types as ua;
use opcua::types::UaNullable as _;
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyIndexError;
use pyo3::exceptions::PyTypeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyAny;
use pyo3::types::PyBytes;
use pyo3::types::PyList;

use crate::time::DateTime;
use crate::time::unix_timestamp;

macro_rules! native_integer {
    ($name:ident $($generic:ident)? ($native:ty, $repr:ty) {
        from($input:ident) => $construct:expr;
        value($inner:ident) => $bits:expr;
        $($constant:ident = $value:expr),* $(,)?
    }) => {
        #[pyclass(frozen, from_py_object, eq, $($generic,)? module = "instro.unstable._opcua")]
        #[derive(Clone, Copy, PartialEq)]
        pub struct $name(pub $native);

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

            fn __int__(&self) -> $repr { self.value() }

            fn __hash__(&self) -> i64 { i64::from(self.value()) }

            fn __repr__(&self) -> String {
                format!("{}({})", stringify!($name), self.value())
            }

            $(
                #[classattr]
                const $constant: Self = Self($value);
            )*
        }
    };
}

native_integer!(AttributeId generic(ua::AttributeId, u32) {
    from(value) => ua::AttributeId::from_u32(value).map_err(|error| PyValueError::new_err(error.to_string()));
    value(inner) => inner as u32;
    NODE_ID = ua::AttributeId::NodeId,
    NODE_CLASS = ua::AttributeId::NodeClass,
    BROWSE_NAME = ua::AttributeId::BrowseName,
    DISPLAY_NAME = ua::AttributeId::DisplayName,
    DESCRIPTION = ua::AttributeId::Description,
    WRITE_MASK = ua::AttributeId::WriteMask,
    USER_WRITE_MASK = ua::AttributeId::UserWriteMask,
    IS_ABSTRACT = ua::AttributeId::IsAbstract,
    SYMMETRIC = ua::AttributeId::Symmetric,
    INVERSE_NAME = ua::AttributeId::InverseName,
    CONTAINS_NO_LOOPS = ua::AttributeId::ContainsNoLoops,
    EVENT_NOTIFIER = ua::AttributeId::EventNotifier,
    VALUE = ua::AttributeId::Value,
    DATA_TYPE = ua::AttributeId::DataType,
    VALUE_RANK = ua::AttributeId::ValueRank,
    ARRAY_DIMENSIONS = ua::AttributeId::ArrayDimensions,
    ACCESS_LEVEL = ua::AttributeId::AccessLevel,
    USER_ACCESS_LEVEL = ua::AttributeId::UserAccessLevel,
    MINIMUM_SAMPLING_INTERVAL = ua::AttributeId::MinimumSamplingInterval,
    HISTORIZING = ua::AttributeId::Historizing,
    EXECUTABLE = ua::AttributeId::Executable,
    USER_EXECUTABLE = ua::AttributeId::UserExecutable,
    DATA_TYPE_DEFINITION = ua::AttributeId::DataTypeDefinition,
    ROLE_PERMISSIONS = ua::AttributeId::RolePermissions,
    USER_ROLE_PERMISSIONS = ua::AttributeId::UserRolePermissions,
    ACCESS_RESTRICTIONS = ua::AttributeId::AccessRestrictions,
    ACCESS_LEVEL_EX = ua::AttributeId::AccessLevelEx,
});

native_integer!(NodeClass(ua::NodeClass, i32) {
    from(value) => ua::NodeClass::try_from(value).map_err(crate::error::native);
    value(inner) => inner as i32;
    UNSPECIFIED = ua::NodeClass::Unspecified,
    OBJECT = ua::NodeClass::Object,
    VARIABLE = ua::NodeClass::Variable,
    METHOD = ua::NodeClass::Method,
    OBJECT_TYPE = ua::NodeClass::ObjectType,
    VARIABLE_TYPE = ua::NodeClass::VariableType,
    REFERENCE_TYPE = ua::NodeClass::ReferenceType,
    DATA_TYPE = ua::NodeClass::DataType,
    VIEW = ua::NodeClass::View,
});

native_integer!(StructureType(ua::StructureType, i32) {
    from(value) => ua::StructureType::try_from(value).map_err(crate::error::native);
    value(inner) => inner as i32;
    STRUCTURE = ua::StructureType::Structure,
    STRUCTURE_WITH_OPTIONAL_FIELDS = ua::StructureType::StructureWithOptionalFields,
    UNION = ua::StructureType::Union,
    STRUCTURE_WITH_SUBTYPED_VALUES = ua::StructureType::StructureWithSubtypedValues,
    UNION_WITH_SUBTYPED_VALUES = ua::StructureType::UnionWithSubtypedValues,
});

native_integer!(VariantType(ua::VariantScalarTypeId, u32) {
    from(value) => ua::VariantScalarTypeId::try_from(value).map_err(|error| PyValueError::new_err(error.to_string()));
    value(inner) => inner as u32;
    BOOLEAN = ua::VariantScalarTypeId::Boolean,
    SBYTE = ua::VariantScalarTypeId::SByte,
    BYTE = ua::VariantScalarTypeId::Byte,
    INT16 = ua::VariantScalarTypeId::Int16,
    UINT16 = ua::VariantScalarTypeId::UInt16,
    INT32 = ua::VariantScalarTypeId::Int32,
    UINT32 = ua::VariantScalarTypeId::UInt32,
    INT64 = ua::VariantScalarTypeId::Int64,
    UINT64 = ua::VariantScalarTypeId::UInt64,
    FLOAT = ua::VariantScalarTypeId::Float,
    DOUBLE = ua::VariantScalarTypeId::Double,
    STRING = ua::VariantScalarTypeId::String,
    DATE_TIME = ua::VariantScalarTypeId::DateTime,
    GUID = ua::VariantScalarTypeId::Guid,
    BYTE_STRING = ua::VariantScalarTypeId::ByteString,
    XML_ELEMENT = ua::VariantScalarTypeId::XmlElement,
    NODE_ID = ua::VariantScalarTypeId::NodeId,
    EXPANDED_NODE_ID = ua::VariantScalarTypeId::ExpandedNodeId,
    STATUS_CODE = ua::VariantScalarTypeId::StatusCode,
    QUALIFIED_NAME = ua::VariantScalarTypeId::QualifiedName,
    LOCALIZED_TEXT = ua::VariantScalarTypeId::LocalizedText,
    EXTENSION_OBJECT = ua::VariantScalarTypeId::ExtensionObject,
    DATA_VALUE = ua::VariantScalarTypeId::DataValue,
    VARIANT = ua::VariantScalarTypeId::Variant,
    DIAGNOSTIC_INFO = ua::VariantScalarTypeId::DiagnosticInfo,
});

native_integer!(AttributeWriteMask(ua::AttributeWriteMask, i32) {
    from(value) => Ok(ua::AttributeWriteMask::from_bits_retain(value));
    value(inner) => inner.bits();
    ACCESS_LEVEL = ua::AttributeWriteMask::AccessLevel,
    ARRAY_DIMENSIONS = ua::AttributeWriteMask::ArrayDimensions,
    BROWSE_NAME = ua::AttributeWriteMask::BrowseName,
    CONTAINS_NO_LOOPS = ua::AttributeWriteMask::ContainsNoLoops,
    DATA_TYPE = ua::AttributeWriteMask::DataType,
    DESCRIPTION = ua::AttributeWriteMask::Description,
    DISPLAY_NAME = ua::AttributeWriteMask::DisplayName,
    EVENT_NOTIFIER = ua::AttributeWriteMask::EventNotifier,
    EXECUTABLE = ua::AttributeWriteMask::Executable,
    HISTORIZING = ua::AttributeWriteMask::Historizing,
    INVERSE_NAME = ua::AttributeWriteMask::InverseName,
    IS_ABSTRACT = ua::AttributeWriteMask::IsAbstract,
    MINIMUM_SAMPLING_INTERVAL = ua::AttributeWriteMask::MinimumSamplingInterval,
    NODE_CLASS = ua::AttributeWriteMask::NodeClass,
    NODE_ID = ua::AttributeWriteMask::NodeId,
    SYMMETRIC = ua::AttributeWriteMask::Symmetric,
    USER_ACCESS_LEVEL = ua::AttributeWriteMask::UserAccessLevel,
    USER_EXECUTABLE = ua::AttributeWriteMask::UserExecutable,
    USER_WRITE_MASK = ua::AttributeWriteMask::UserWriteMask,
    VALUE_RANK = ua::AttributeWriteMask::ValueRank,
    WRITE_MASK = ua::AttributeWriteMask::WriteMask,
    VALUE_FOR_VARIABLE_TYPE = ua::AttributeWriteMask::ValueForVariableType,
    DATA_TYPE_DEFINITION = ua::AttributeWriteMask::DataTypeDefinition,
    ROLE_PERMISSIONS = ua::AttributeWriteMask::RolePermissions,
    ACCESS_RESTRICTIONS = ua::AttributeWriteMask::AccessRestrictions,
    ACCESS_LEVEL_EX = ua::AttributeWriteMask::AccessLevelEx,
});

native_integer!(AccessLevelType(ua::AccessLevelType, u8) {
    from(value) => Ok(ua::AccessLevelType::from_bits_retain(value));
    value(inner) => inner.bits();
    CURRENT_READ = ua::AccessLevelType::CurrentRead,
    CURRENT_WRITE = ua::AccessLevelType::CurrentWrite,
    HISTORY_READ = ua::AccessLevelType::HistoryRead,
    HISTORY_WRITE = ua::AccessLevelType::HistoryWrite,
    SEMANTIC_CHANGE = ua::AccessLevelType::SemanticChange,
    STATUS_WRITE = ua::AccessLevelType::StatusWrite,
    TIMESTAMP_WRITE = ua::AccessLevelType::TimestampWrite,
});

native_integer!(AccessLevelExType(ua::AccessLevelExType, i32) {
    from(value) => Ok(ua::AccessLevelExType::from_bits_retain(value));
    value(inner) => inner.bits();
    CURRENT_READ = ua::AccessLevelExType::CurrentRead,
    CURRENT_WRITE = ua::AccessLevelExType::CurrentWrite,
    HISTORY_READ = ua::AccessLevelExType::HistoryRead,
    HISTORY_WRITE = ua::AccessLevelExType::HistoryWrite,
    SEMANTIC_CHANGE = ua::AccessLevelExType::SemanticChange,
    STATUS_WRITE = ua::AccessLevelExType::StatusWrite,
    TIMESTAMP_WRITE = ua::AccessLevelExType::TimestampWrite,
    NONATOMIC_READ = ua::AccessLevelExType::NonatomicRead,
    NONATOMIC_WRITE = ua::AccessLevelExType::NonatomicWrite,
    WRITE_FULL_ARRAY_ONLY = ua::AccessLevelExType::WriteFullArrayOnly,
    NO_SUB_DATA_TYPES = ua::AccessLevelExType::NoSubDataTypes,
    NON_VOLATILE = ua::AccessLevelExType::NonVolatile,
    CONSTANT = ua::AccessLevelExType::Constant,
});

native_integer!(AccessRestrictionType(ua::AccessRestrictionType, i16) {
    from(value) => Ok(ua::AccessRestrictionType::from_bits_retain(value));
    value(inner) => inner.bits();
    SIGNING_REQUIRED = ua::AccessRestrictionType::SigningRequired,
    ENCRYPTION_REQUIRED = ua::AccessRestrictionType::EncryptionRequired,
    SESSION_REQUIRED = ua::AccessRestrictionType::SessionRequired,
    APPLY_RESTRICTIONS_TO_BROWSE = ua::AccessRestrictionType::ApplyRestrictionsToBrowse,
});

native_integer!(EventNotifierType(ua::EventNotifierType, u8) {
    from(value) => Ok(ua::EventNotifierType::from_bits_retain(value));
    value(inner) => inner.bits();
    SUBSCRIBE_TO_EVENTS = ua::EventNotifierType::SubscribeToEvents,
    HISTORY_READ = ua::EventNotifierType::HistoryRead,
    HISTORY_WRITE = ua::EventNotifierType::HistoryWrite,
});

native_integer!(PermissionType(ua::PermissionType, i32) {
    from(value) => Ok(ua::PermissionType::from_bits_retain(value));
    value(inner) => inner.bits();
    BROWSE = ua::PermissionType::Browse,
    READ_ROLE_PERMISSIONS = ua::PermissionType::ReadRolePermissions,
    WRITE_ATTRIBUTE = ua::PermissionType::WriteAttribute,
    WRITE_ROLE_PERMISSIONS = ua::PermissionType::WriteRolePermissions,
    WRITE_HISTORIZING = ua::PermissionType::WriteHistorizing,
    READ = ua::PermissionType::Read,
    WRITE = ua::PermissionType::Write,
    READ_HISTORY = ua::PermissionType::ReadHistory,
    INSERT_HISTORY = ua::PermissionType::InsertHistory,
    MODIFY_HISTORY = ua::PermissionType::ModifyHistory,
    DELETE_HISTORY = ua::PermissionType::DeleteHistory,
    RECEIVE_EVENTS = ua::PermissionType::ReceiveEvents,
    CALL = ua::PermissionType::Call,
    ADD_REFERENCE = ua::PermissionType::AddReference,
    REMOVE_REFERENCE = ua::PermissionType::RemoveReference,
    DELETE_NODE = ua::PermissionType::DeleteNode,
    ADD_NODE = ua::PermissionType::AddNode,
});

macro_rules! native_record {
    ($name:ident { $($field:ident: $ty:ty = $value:ident => $convert:expr),* $(,)? }) => {
        #[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
        #[derive(Clone)]
        pub struct $name(pub ua::$name);

        #[pymethods]
        impl $name {
            $(
                #[getter]
                fn $field(&self) -> $ty {
                    let $value = &self.0;
                    $convert
                }
            )*
        }
    };
}

native_record!(RolePermissionType {
    role_id: NodeId = value => NodeId(value.role_id.clone()),
    permissions: PermissionType = value => PermissionType(value.permissions),
});
native_record!(StructureField {
    name: Option<String> = value => value.name.value().clone(),
    description: LocalizedText = value => LocalizedText(value.description.clone()),
    data_type: NodeId = value => NodeId(value.data_type.clone()),
    value_rank: i32 = value => value.value_rank,
    array_dimensions: Option<Vec<u32>> = value => value.array_dimensions.clone(),
    max_string_length: u32 = value => value.max_string_length,
    is_optional: bool = value => value.is_optional,
});
native_record!(StructureDefinition {
    default_encoding_id: NodeId = value => NodeId(value.default_encoding_id.clone()),
    base_data_type: NodeId = value => NodeId(value.base_data_type.clone()),
    structure_type: StructureType = value => StructureType(value.structure_type),
    fields: Option<Vec<StructureField>> = value => value.fields.as_ref().map(|fields| fields.iter().cloned().map(StructureField).collect()),
});
native_record!(EnumField {
    name: Option<String> = value => value.name.value().clone(),
    value: i64 = field => field.value,
    display_name: LocalizedText = value => LocalizedText(value.display_name.clone()),
    description: LocalizedText = value => LocalizedText(value.description.clone()),
});
native_record!(EnumDefinition {
    fields: Option<Vec<EnumField>> = value => value.fields.as_ref().map(|fields| fields.iter().cloned().map(EnumField).collect()),
});
native_record!(BrowsePath {
    starting_node: NodeId = value => NodeId(value.starting_node.clone()),
    relative_path: RelativePath = value => RelativePath(value.relative_path.clone()),
});

#[pyclass(frozen, from_py_object, eq, hash, module = "instro.unstable._opcua")]
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct StatusCode(pub ua::StatusCode);

#[pymethods]
impl StatusCode {
    #[new]
    fn new(value: u32) -> Self {
        Self(value.into())
    }

    #[getter]
    fn value(&self) -> u32 {
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

    fn __int__(&self) -> u32 {
        self.0.bits()
    }

    fn __str__(&self) -> String {
        self.0.to_string()
    }

    fn __repr__(&self) -> String {
        format!("StatusCode(0x{:08x})", self.0.bits())
    }
}

#[pyclass(frozen, from_py_object, eq, hash, module = "instro.unstable._opcua")]
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct NodeId(pub ua::NodeId);

#[pymethods]
impl NodeId {
    #[new]
    fn new(text: &str) -> PyResult<Self> {
        ua::NodeId::from_str(text)
            .map(Self)
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    #[getter]
    fn namespace_index(&self) -> u16 {
        self.0.namespace
    }

    #[getter]
    fn identifier(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.0.identifier {
            ua::Identifier::Numeric(value) => value.into_py_any(py),
            ua::Identifier::String(value) => value.value().into_py_any(py),
            ua::Identifier::Guid(value) => Guid(value.clone()).into_py_any(py),
            ua::Identifier::ByteString(value) => bytes(py, value),
        }
    }

    fn __str__(&self) -> String {
        self.0.to_string()
    }

    fn __repr__(&self) -> String {
        format!("NodeId({:?})", self.0.to_string())
    }
}

#[pyclass(frozen, from_py_object, eq, module = "instro.unstable._opcua")]
#[derive(Clone, PartialEq)]
pub struct QualifiedName(pub ua::QualifiedName);

#[pymethods]
impl QualifiedName {
    #[new]
    fn new(namespace_index: u16, name: String) -> Self {
        Self(ua::QualifiedName::new(namespace_index, name))
    }

    #[getter]
    fn namespace_index(&self) -> u16 {
        self.0.namespace_index
    }

    #[getter]
    fn name(&self) -> Option<String> {
        self.0.name.value().clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "QualifiedName({}, {:?})",
            self.0.namespace_index,
            self.0.name.value()
        )
    }
}

#[pyclass(frozen, from_py_object, eq, module = "instro.unstable._opcua")]
#[derive(Clone, PartialEq)]
pub struct LocalizedText(pub ua::LocalizedText);

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

    #[getter]
    fn text(&self) -> Option<String> {
        self.0.text.value().clone()
    }

    #[getter]
    fn locale(&self) -> Option<String> {
        self.0.locale.value().clone()
    }
}

#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct ExpandedNodeId(pub ua::ExpandedNodeId);

#[pymethods]
impl ExpandedNodeId {
    #[getter]
    fn node_id(&self) -> NodeId {
        NodeId(self.0.node_id.clone())
    }

    #[getter]
    fn namespace_uri(&self) -> Option<String> {
        self.0.namespace_uri.value().clone()
    }

    #[getter]
    fn server_index(&self) -> u32 {
        self.0.server_index
    }
}

#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct RelativePath(pub ua::RelativePath);

#[pymethods]
impl RelativePath {
    #[new]
    fn new(text: &str) -> PyResult<Self> {
        ua::RelativePath::try_from(text)
            .map(Self)
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    #[staticmethod]
    fn from_qualified_names(names: Vec<QualifiedName>) -> Self {
        let names: Vec<_> = names.into_iter().map(|name| name.0).collect();
        Self(ua::RelativePath::from(names.as_slice()))
    }

    fn __str__(&self) -> String {
        String::from(&self.0)
    }
}

#[pyclass(frozen, from_py_object, generic, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct Array {
    pub inner: ua::Array,
    pub attribute: Option<ua::AttributeId>,
}

#[pymethods]
impl Array {
    #[getter]
    fn element_type(&self) -> VariantType {
        VariantType(self.inner.value_type)
    }

    #[getter]
    fn dimensions(&self) -> Option<Vec<u32>> {
        self.inner.dimensions.clone()
    }

    #[getter]
    fn values(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let values = self
            .inner
            .values
            .iter()
            .map(|value| convert(py, value, self.attribute))
            .collect::<PyResult<Vec<_>>>()?;
        Ok(PyList::new(py, values)?.into_any().unbind())
    }

    fn __len__(&self) -> usize {
        self.inner.values.len()
    }

    fn __getitem__(&self, py: Python<'_>, index: isize) -> PyResult<Py<PyAny>> {
        let len = self.inner.values.len() as isize;
        let index = if index < 0 { index + len } else { index };
        let value = usize::try_from(index)
            .ok()
            .and_then(|index| self.inner.values.get(index))
            .ok_or_else(|| PyIndexError::new_err(index))?;
        convert(py, value, self.attribute)
    }
}

#[pyclass(frozen, from_py_object, generic, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct DataValue {
    pub inner: ua::DataValue,
    pub attribute: Option<ua::AttributeId>,
}

#[pymethods]
impl DataValue {
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.inner.value {
            Some(value) => {
                if let Some(attribute) = self.attribute {
                    validate_attribute_payload(attribute, value)?;
                }
                convert(py, value, self.attribute)
            }
            None => Ok(py.None()),
        }
    }

    #[getter]
    fn status(&self) -> Option<StatusCode> {
        self.inner.status.map(StatusCode)
    }

    #[getter]
    fn source_timestamp(&self) -> Option<DateTime> {
        self.inner.source_timestamp.map(DateTime)
    }

    #[getter]
    fn server_timestamp(&self) -> Option<DateTime> {
        self.inner.server_timestamp.map(DateTime)
    }

    #[getter]
    fn source_picoseconds(&self) -> Option<u16> {
        self.inner.source_picoseconds
    }

    #[getter]
    fn server_picoseconds(&self) -> Option<u16> {
        self.inner.server_picoseconds
    }

    #[getter]
    fn source_unix_timestamp(&self) -> Option<i128> {
        unix_timestamp(self.inner.source_timestamp, self.inner.source_picoseconds)
    }

    #[getter]
    fn server_unix_timestamp(&self) -> Option<i128> {
        unix_timestamp(self.inner.server_timestamp, self.inner.server_picoseconds)
    }
}

fn bytes(py: Python<'_>, value: &ua::ByteString) -> PyResult<Py<PyAny>> {
    if value.is_null() {
        Ok(py.None())
    } else {
        Ok(PyBytes::new(py, value.as_ref()).into_any().unbind())
    }
}

#[pyclass(frozen, from_py_object, eq, hash, module = "instro.unstable._opcua")]
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct Guid(pub ua::Guid);

#[pymethods]
impl Guid {
    #[new]
    fn new(text: &str) -> PyResult<Self> {
        ua::Guid::from_str(text)
            .map(Self)
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    fn __str__(&self) -> String {
        self.0.to_string()
    }
}

fn validate_attribute_payload(attribute: ua::AttributeId, value: &ua::Variant) -> PyResult<()> {
    use ua::AttributeId as Attribute;
    use ua::Variant;

    let valid = match attribute {
        Attribute::Value => true,
        Attribute::NodeId | Attribute::DataType => matches!(value, Variant::NodeId(_)),
        Attribute::NodeClass | Attribute::ValueRank => matches!(value, Variant::Int32(_)),
        Attribute::BrowseName => matches!(value, Variant::QualifiedName(_)),
        Attribute::DisplayName | Attribute::Description | Attribute::InverseName => {
            matches!(value, Variant::LocalizedText(_))
        }
        Attribute::WriteMask | Attribute::UserWriteMask | Attribute::AccessLevelEx => {
            matches!(value, Variant::UInt32(_))
        }
        Attribute::IsAbstract
        | Attribute::Symmetric
        | Attribute::ContainsNoLoops
        | Attribute::Historizing
        | Attribute::Executable
        | Attribute::UserExecutable => {
            matches!(value, Variant::Boolean(_))
        }
        Attribute::EventNotifier | Attribute::AccessLevel | Attribute::UserAccessLevel => {
            matches!(value, Variant::Byte(_))
        }
        Attribute::ArrayDimensions => {
            matches!(value, Variant::Array(array) if array.value_type == ua::VariantScalarTypeId::UInt32)
        }
        Attribute::MinimumSamplingInterval => matches!(value, Variant::Double(_)),
        Attribute::DataTypeDefinition => matches!(value, Variant::ExtensionObject(_)),
        Attribute::RolePermissions | Attribute::UserRolePermissions => {
            matches!(value, Variant::Array(array) if array.value_type == ua::VariantScalarTypeId::ExtensionObject)
        }
        Attribute::AccessRestrictions => matches!(value, Variant::UInt16(_)),
    };
    if valid || matches!(value, Variant::Empty) {
        Ok(())
    } else {
        Err(PyTypeError::new_err(format!(
            "native payload for {attribute:?} has an incompatible OPC UA type"
        )))
    }
}

pub fn convert(
    py: Python<'_>,
    value: &ua::Variant,
    attribute: Option<ua::AttributeId>,
) -> PyResult<Py<PyAny>> {
    use ua::Variant;

    match (attribute, value) {
        (Some(ua::AttributeId::NodeClass), Variant::Int32(value)) => {
            return NodeClass(ua::NodeClass::try_from(*value).map_err(crate::error::native)?)
                .into_py_any(py);
        }
        (
            Some(ua::AttributeId::WriteMask | ua::AttributeId::UserWriteMask),
            Variant::UInt32(value),
        ) => {
            return AttributeWriteMask(ua::AttributeWriteMask::from_bits_retain(*value as i32))
                .into_py_any(py);
        }
        (
            Some(ua::AttributeId::AccessLevel | ua::AttributeId::UserAccessLevel),
            Variant::Byte(value),
        ) => return AccessLevelType(ua::AccessLevelType::from_bits_retain(*value)).into_py_any(py),
        (Some(ua::AttributeId::AccessLevelEx), Variant::UInt32(value)) => {
            return AccessLevelExType(ua::AccessLevelExType::from_bits_retain(*value as i32))
                .into_py_any(py);
        }
        (Some(ua::AttributeId::AccessRestrictions), Variant::UInt16(value)) => {
            return AccessRestrictionType(ua::AccessRestrictionType::from_bits_retain(
                *value as i16,
            ))
            .into_py_any(py);
        }
        (Some(ua::AttributeId::EventNotifier), Variant::Byte(value)) => {
            return EventNotifierType(ua::EventNotifierType::from_bits_retain(*value))
                .into_py_any(py);
        }
        _ => {}
    }

    match value {
        Variant::Empty => Ok(py.None()),
        Variant::Boolean(value) => value.into_py_any(py),
        Variant::SByte(value) => value.into_py_any(py),
        Variant::Byte(value) => value.into_py_any(py),
        Variant::Int16(value) => value.into_py_any(py),
        Variant::UInt16(value) => value.into_py_any(py),
        Variant::Int32(value) => value.into_py_any(py),
        Variant::UInt32(value) => value.into_py_any(py),
        Variant::Int64(value) => value.into_py_any(py),
        Variant::UInt64(value) => value.into_py_any(py),
        Variant::Float(value) => value.into_py_any(py),
        Variant::Double(value) => value.into_py_any(py),
        Variant::String(value) => value.value().into_py_any(py),
        Variant::DateTime(value) => DateTime(**value).into_py_any(py),
        Variant::Guid(value) => Guid((**value).clone()).into_py_any(py),
        Variant::StatusCode(value) => StatusCode(*value).into_py_any(py),
        Variant::ByteString(value) => bytes(py, value),
        Variant::XmlElement(value) => {
            if value.is_ua_null() {
                Ok(py.None())
            } else {
                value.to_string().into_py_any(py)
            }
        }
        Variant::QualifiedName(value) => QualifiedName((**value).clone()).into_py_any(py),
        Variant::LocalizedText(value) => LocalizedText((**value).clone()).into_py_any(py),
        Variant::NodeId(value) => NodeId((**value).clone()).into_py_any(py),
        Variant::ExpandedNodeId(value) => ExpandedNodeId((**value).clone()).into_py_any(py),
        Variant::Variant(value) => convert(py, value, None),
        Variant::Array(value) => Array {
            inner: (**value).clone(),
            attribute,
        }
        .into_py_any(py),
        Variant::DataValue(value) => DataValue {
            inner: (**value).clone(),
            attribute: None,
        }
        .into_py_any(py),
        Variant::ExtensionObject(value) => {
            if let Some(body) = &value.body {
                let body = body.as_dyn_any_ref();
                if let Some(value) = body.downcast_ref::<ua::RolePermissionType>() {
                    return RolePermissionType(value.clone()).into_py_any(py);
                }
                if let Some(value) = body.downcast_ref::<ua::StructureDefinition>() {
                    return StructureDefinition(value.clone()).into_py_any(py);
                }
                if let Some(value) = body.downcast_ref::<ua::EnumDefinition>() {
                    return EnumDefinition(value.clone()).into_py_any(py);
                }
            }
            ExtensionObject(value.clone()).into_py_any(py)
        }
        Variant::DiagnosticInfo(value) => DiagnosticInfo((**value).clone()).into_py_any(py),
    }
}

#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct ExtensionObject(pub ua::ExtensionObject);

#[pymethods]
impl ExtensionObject {
    #[new]
    fn new(type_id: NodeId, body: Vec<u8>) -> Self {
        Self(ua::ExtensionObject::new(ua::ByteStringBody::new(
            body.into(),
            type_id.0,
        )))
    }

    #[getter]
    fn type_id(&self) -> ExpandedNodeId {
        ExpandedNodeId(self.0.binary_type_id())
    }

    #[getter]
    fn encoding(&self) -> &'static str {
        match self.0.body.as_ref() {
            None => "none",
            Some(body)
                if matches!(body.override_encoding(), Some(ua::BuiltInDataEncoding::XML)) =>
            {
                "xml"
            }
            Some(_) => "binary",
        }
    }

    #[getter]
    fn body(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let Some(body) = &self.0.body else {
            return Ok(py.None());
        };
        let mut bytes = Vec::new();
        body.encode_binary(&mut bytes, &ua::ContextOwned::default().context())
            .map_err(crate::error::native)?;
        Ok(PyBytes::new(py, &bytes).into_any().unbind())
    }
}

#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct DiagnosticInfo(pub ua::DiagnosticInfo);

#[pymethods]
impl DiagnosticInfo {
    #[getter]
    fn symbolic_id(&self) -> Option<i32> {
        self.0.symbolic_id
    }
    #[getter]
    fn namespace_uri(&self) -> Option<i32> {
        self.0.namespace_uri
    }
    #[getter]
    fn locale(&self) -> Option<i32> {
        self.0.locale
    }
    #[getter]
    fn localized_text(&self) -> Option<i32> {
        self.0.localized_text
    }
    #[getter]
    fn additional_info(&self) -> Option<String> {
        self.0
            .additional_info
            .as_ref()
            .and_then(|value| value.value().clone())
    }
    #[getter]
    fn inner_status_code(&self) -> Option<StatusCode> {
        self.0.inner_status_code.map(StatusCode)
    }
    #[getter]
    fn inner_diagnostic_info(&self) -> Option<DiagnosticInfo> {
        self.0
            .inner_diagnostic_info
            .as_ref()
            .map(|value| DiagnosticInfo((**value).clone()))
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<AttributeId>()?;
    module.add_class::<NodeClass>()?;
    module.add_class::<VariantType>()?;
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
    module.add_class::<Guid>()?;
    module.add_class::<QualifiedName>()?;
    module.add_class::<LocalizedText>()?;
    module.add_class::<ExpandedNodeId>()?;
    module.add_class::<RelativePath>()?;
    module.add_class::<Array>()?;
    module.add_class::<DataValue>()?;
    module.add_class::<ExtensionObject>()?;
    module.add_class::<DiagnosticInfo>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_attribute_payloads_cannot_violate_their_python_annotations() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let mut sample = DataValue {
                inner: ua::DataValue {
                    value: Some(ua::Variant::Double(42.5)),
                    ..Default::default()
                },
                attribute: Some(ua::AttributeId::DisplayName),
            };
            let error = sample.value(py).expect_err("DisplayName accepted a Double");
            assert!(error.is_instance_of::<PyTypeError>(py));
            sample.attribute = Some(ua::AttributeId::Value);
            assert_eq!(sample.value(py)?.extract::<f64>(py)?, 42.5);
            Ok(())
        })
    }

    #[test]
    fn integer_conversion_preserves_signed_and_unsigned_limits() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            for (value, expected) in [
                (ua::Variant::Int64(i64::MIN), i128::from(i64::MIN)),
                (ua::Variant::UInt64(u64::MAX), i128::from(u64::MAX)),
            ] {
                assert_eq!(convert(py, &value, None)?.extract::<i128>(py)?, expected);
            }
            Ok(())
        })
    }

    #[test]
    fn bad_and_uncertain_samples_keep_payload_and_native_metadata() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let timestamp = ua::DateTime::from(116_444_736_000_000_001_i64);
            for status in [
                ua::StatusCode::BadUserAccessDenied,
                ua::StatusCode::Uncertain,
            ] {
                let value = DataValue {
                    inner: ua::DataValue {
                        value: Some(ua::Variant::Double(42.5)),
                        status: Some(status),
                        source_timestamp: Some(timestamp),
                        source_picoseconds: Some(1_399),
                        ..Default::default()
                    },
                    attribute: Some(ua::AttributeId::Value),
                };
                assert_eq!(value.value(py)?.extract::<f64>(py)?, 42.5);
                assert_eq!(value.status().map(|status| status.0), Some(status));
                assert_eq!(
                    value.source_timestamp().map(|value| value.0),
                    Some(timestamp)
                );
                assert_eq!(value.source_picoseconds(), Some(1_399));
                assert_eq!(value.source_unix_timestamp(), Some(113));
                assert_eq!(value.server_unix_timestamp(), None);
            }
            Ok(())
        })
    }

    #[test]
    fn empty_arrays_keep_the_type_and_dimensions_supplied_by_upstream() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            for dimensions in [None, Some(vec![0, 3])] {
                let value = ua::Variant::Array(Box::new(ua::Array {
                    value_type: ua::VariantScalarTypeId::Double,
                    values: Vec::new(),
                    dimensions: dimensions.clone(),
                }));
                let converted = convert(py, &value, None)?;
                let array = converted.bind(py).cast::<Array>()?.borrow();
                assert_eq!(array.element_type().0, ua::VariantScalarTypeId::Double);
                assert_eq!(array.dimensions(), dimensions);
                assert_eq!(array.__len__(), 0);
            }
            Ok(())
        })
    }

    #[test]
    fn datetime_array_elements_remain_native_datetime_objects() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let timestamp = ua::DateTime::from(116_444_736_000_000_001_i64);
            let array = Array {
                inner: ua::Array::new(
                    ua::VariantScalarTypeId::DateTime,
                    vec![ua::Variant::DateTime(Box::new(timestamp))],
                )
                .map_err(|error| PyValueError::new_err(error.to_string()))?,
                attribute: None,
            };
            let value = array.__getitem__(py, 0)?;
            assert_eq!(value.bind(py).cast::<DateTime>()?.borrow().0, timestamp);
            assert!(array.__getitem__(py, 1).is_err());
            Ok(())
        })
    }

    #[test]
    fn unknown_extension_object_keeps_its_identifier_encoding_and_body() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let id = ua::NodeId::new(12, 12345_u32);
            let bytes = vec![0, 128, 255];
            let raw =
                ua::ExtensionObject::new(ua::ByteStringBody::new(bytes.clone().into(), id.clone()));
            let value = convert(py, &ua::Variant::ExtensionObject(raw), None)?;
            let value = value.bind(py).cast::<ExtensionObject>()?.borrow();
            assert_eq!(value.type_id().0.node_id, id);
            assert_eq!(value.encoding(), "binary");
            assert_eq!(value.body(py)?.extract::<Vec<u8>>(py)?, bytes);
            Ok(())
        })
    }

    #[test]
    fn node_class_payload_is_the_native_enum_wrapper() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let value = convert(py, &ua::Variant::Int32(2), Some(ua::AttributeId::NodeClass))?;
            assert_eq!(
                value.bind(py).cast::<NodeClass>()?.borrow().0,
                ua::NodeClass::Variable
            );
            Ok(())
        })
    }
}
