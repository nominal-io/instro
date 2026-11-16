use opcua::types as ua;
use pyo3::prelude::*;

use super::UaType;
use super::payload;
use super::payload::Payload;
use super::wrappers::*;
use crate::time::DateTime;
use crate::time::unix_timestamp;

macro_rules! attributes {
    ($($name:ident: $native:ty => $wrapper:ty),* $(,)?) => {
        const _: fn() = || {
            fn assert_payload<T: Payload<Native = N>, N>() {}
            $(assert_payload::<$wrapper, $native>();)*
        };

        pub(crate) fn named_attribute(name: &str) -> Option<ua::AttributeId> {
            paste::paste! {
                match name.to_ascii_lowercase().as_str() {
                    $(
                        stringify!([<$name:lower>]) | stringify!([<$name:snake>]) =>
                            Some(ua::AttributeId::$name),
                    )*
                    _ => None,
                }
            }
        }

        fn convert_attribute(id: ua::AttributeId, value: &ua::Variant, py: Python<'_>) -> PyResult<Py<PyAny>> {
            match id {
                $(ua::AttributeId::$name => <$wrapper>::decode(value)?.to_python(py),)*
            }
        }
    };
}

// Attribute definitions are also the dispatch schema; conversion and validation use their payload types.
attributes! {
    NodeId: ua::NodeId => super::NodeId,
    NodeClass: ua::NodeClass => super::NodeClass,
    BrowseName: ua::QualifiedName => QualifiedName,
    DisplayName: ua::LocalizedText => LocalizedText,
    Description: ua::LocalizedText => LocalizedText,
    WriteMask: ua::AttributeWriteMask => AttributeWriteMask,
    UserWriteMask: ua::AttributeWriteMask => AttributeWriteMask,
    IsAbstract: bool => bool,
    Symmetric: bool => bool,
    InverseName: ua::LocalizedText => LocalizedText,
    ContainsNoLoops: bool => bool,
    EventNotifier: ua::EventNotifierType => EventNotifierType,
    Value: payload::BoundedValue => payload::BoundedValue,
    DataType: ua::NodeId => super::NodeId,
    ValueRank: i32 => i32,
    ArrayDimensions: Vec<u32> => Vec<u32>,
    AccessLevel: ua::AccessLevelType => AccessLevelType,
    UserAccessLevel: ua::AccessLevelType => AccessLevelType,
    MinimumSamplingInterval: f64 => payload::SamplingInterval,
    Historizing: bool => bool,
    Executable: bool => bool,
    UserExecutable: bool => bool,
    DataTypeDefinition: ua::DataTypeDefinition => payload::Definition,
    RolePermissions: Vec<ua::RolePermissionType> => Vec<RolePermissionType>,
    UserRolePermissions: Vec<ua::RolePermissionType> => Vec<RolePermissionType>,
    AccessRestrictions: ua::AccessRestrictionType => AccessRestrictionType,
    AccessLevelEx: ua::AccessLevelExType => AccessLevelExType,
}

/// An attribute-aware result with its semantic payload and original quality metadata.
///
/// Returned by Node reads and subscriptions; it is not a DataValue subclass and
/// provides no raw DataValue accessor. Missing payloads become ``None``. Bad or
/// Uncertain quality alone does not raise. The payload is converted before this
/// object is returned. Access ``value`` to retrieve it; a failed conversion
/// raises then and leaves the metadata intact.
#[pyclass(
    name = "AttributeValue",
    frozen,
    from_py_object,
    generic,
    module = "instro.unstable.opcua.types"
)]
pub(crate) struct PyAttributeValue {
    attribute_id: ua::AttributeId,
    value: PyResult<Py<PyAny>>,
    status: Option<ua::StatusCode>,
    source_timestamp: Option<ua::DateTime>,
    source_picoseconds: Option<u16>,
    server_timestamp: Option<ua::DateTime>,
    server_picoseconds: Option<u16>,
}

impl PyAttributeValue {
    pub(crate) fn from_native(
        py: Python<'_>,
        attribute_id: ua::AttributeId,
        native: ua::DataValue,
    ) -> PyResult<Self> {
        let ua::DataValue {
            value,
            status,
            source_timestamp,
            source_picoseconds,
            server_timestamp,
            server_picoseconds,
        } = native;

        let value = match value {
            None | Some(ua::Variant::Empty) => Ok(py.None()),
            Some(value) => convert_attribute(attribute_id, &value, py),
        };

        Ok(Self {
            attribute_id,
            value,
            status,
            source_timestamp,
            source_picoseconds,
            server_timestamp,
            server_picoseconds,
        })
    }
}

impl Clone for PyAttributeValue {
    fn clone(&self) -> Self {
        let value = Python::attach(|py| match &self.value {
            Ok(value) => Ok(value.clone_ref(py)),
            Err(error) => Err(error.clone_ref(py)),
        });

        Self { value, ..*self }
    }
}

#[pymethods]
impl PyAttributeValue {
    /// object | None: The semantic payload.
    ///
    /// Raises:
    ///     OpcUaError: ``BadTypeMismatch`` for an incompatible fixed attribute
    ///         payload, or ``BadNotSupported`` for unsupported types or dimensions.
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.value {
            Ok(value) => Ok(value.clone_ref(py)),
            Err(error) => Err(error.clone_ref(py)),
        }
    }

    /// AttributeId: The attribute associated with this result.
    #[getter]
    fn attribute_id(&self) -> AttributeId {
        AttributeId::new(self.attribute_id)
    }

    /// int | None: Original source fractional field, in units of 10 picoseconds.
    #[getter]
    fn source_picoseconds(&self) -> Option<u16> {
        self.source_picoseconds
    }

    /// int | None: Original server fractional field, in units of 10 picoseconds.
    #[getter]
    fn server_picoseconds(&self) -> Option<u16> {
        self.server_picoseconds
    }

    /// StatusCode: Original sample quality; defaults to Good when absent.
    #[getter]
    fn status(&self) -> StatusCode {
        let status = self.status.unwrap_or(ua::StatusCode::Good);
        StatusCode::new(status)
    }

    /// DateTime | None: Native source timestamp, without the fractional field.
    #[getter]
    fn source_timestamp(&self) -> Option<DateTime> {
        self.source_timestamp.map(DateTime::new)
    }

    /// DateTime | None: Native server timestamp, without the fractional field.
    #[getter]
    fn server_timestamp(&self) -> Option<DateTime> {
        self.server_timestamp.map(DateTime::new)
    }

    /// int | None: Source Unix nanoseconds, including the truncated fractional field.
    #[getter]
    fn source_unix_timestamp(&self) -> Option<i128> {
        unix_timestamp(self.source_timestamp, self.source_picoseconds)
    }

    /// int | None: Server Unix nanoseconds, including the truncated fractional field.
    #[getter]
    fn server_unix_timestamp(&self) -> Option<i128> {
        unix_timestamp(self.server_timestamp, self.server_picoseconds)
    }
}
