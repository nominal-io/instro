use opcua::types as ua;
use pyo3::prelude::*;

use super::payload;
use super::payload::ReadValue;
use super::wrappers::*;
use crate::time::DateTime;
use crate::time::unix_timestamp;

macro_rules! attributes {
    ($($name:ident => $read:path),* $(,)?) => {
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
                $(ua::AttributeId::$name => $read(py, value),)*
            }
        }
    };
}

attributes! {
    NodeId => NodeId::read,
    NodeClass => NodeClass::read,
    BrowseName => QualifiedName::read,
    DisplayName => LocalizedText::read,
    Description => LocalizedText::read,
    WriteMask => AttributeWriteMask::read,
    UserWriteMask => AttributeWriteMask::read,
    IsAbstract => bool::read,
    Symmetric => bool::read,
    InverseName => LocalizedText::read,
    ContainsNoLoops => bool::read,
    EventNotifier => EventNotifierType::read,
    Value => payload::read_value,
    DataType => NodeId::read,
    ValueRank => i32::read,
    ArrayDimensions => payload::read_list::<u32>,
    AccessLevel => AccessLevelType::read,
    UserAccessLevel => AccessLevelType::read,
    MinimumSamplingInterval => payload::read_sampling_interval,
    Historizing => bool::read,
    Executable => bool::read,
    UserExecutable => bool::read,
    DataTypeDefinition => payload::read_definition,
    RolePermissions => payload::read_list::<RolePermissionType>,
    UserRolePermissions => payload::read_list::<RolePermissionType>,
    AccessRestrictions => AccessRestrictionType::read,
    AccessLevelEx => AccessLevelExType::read,
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
    ) -> Self {
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

        Self {
            attribute_id,
            value,
            status,
            source_timestamp,
            source_picoseconds,
            server_timestamp,
            server_picoseconds,
        }
    }
}

impl Clone for PyAttributeValue {
    fn clone(&self) -> Self {
        let value = Python::attach(|py| self.value(py));

        Self { value, ..*self }
    }
}

#[pymethods]
impl PyAttributeValue {
    /// object | None: The semantic payload.
    ///
    /// Raises:
    ///     OpcUaError: ``BadTypeMismatch`` for an incompatible fixed attribute
    ///         payload, ``BadNotSupported`` for unsupported types or dimensions,
    ///         or ``BadOutOfRange`` for an unrepresentable sampling interval.
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
        self.attribute_id.into()
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
        status.into()
    }

    /// DateTime | None: Native source timestamp, without the fractional field.
    #[getter]
    fn source_timestamp(&self) -> Option<DateTime> {
        self.source_timestamp.map(DateTime::from)
    }

    /// DateTime | None: Native server timestamp, without the fractional field.
    #[getter]
    fn server_timestamp(&self) -> Option<DateTime> {
        self.server_timestamp.map(DateTime::from)
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
