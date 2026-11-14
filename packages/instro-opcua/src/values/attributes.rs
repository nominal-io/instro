use std::sync::OnceLock;

use opcua::types as ua;
use pyo3::prelude::*;

use super::UaType;
use super::payload;
use super::payload::Payload;
use super::payload::PayloadError;
use super::wrappers::*;
use crate::time::DateTime;
use crate::time::unix_timestamp;

pub(crate) trait Attribute {
    type Native: Clone;
    type Value: Payload<Native = Self::Native>;

    const ID: ua::AttributeId;

    fn into_python(value: AttributeValue<Self::Value>) -> PyAttributeValue;

    fn wrap(value: ua::DataValue) -> AttributeValue<Self::Value> {
        AttributeValue {
            attribute_id: Self::ID,
            native: value,
            payload: OnceLock::new(),
        }
    }
}

#[derive(Clone)]
pub(crate) struct AttributeValue<T: Payload> {
    attribute_id: ua::AttributeId,
    native: ua::DataValue,
    payload: OnceLock<Result<Option<T>, PayloadError>>,
}

impl<T: Payload> AttributeValue<T> {
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let payload = self.payload.get_or_init(|| match &self.native.value {
            None | Some(ua::Variant::Empty) => Ok(None),
            Some(value) => T::decode(value).map(Some),
        });

        match payload {
            Ok(Some(value)) => value.to_python(py),
            Ok(None) => Ok(py.None()),
            Err(error) => Err((*error).into()),
        }
    }
}

macro_rules! attributes {
    ($($name:ident: $native:ty => $wrapper:ty),* $(,)?) => {
        $(
            pub(crate) struct $name;

            impl Attribute for $name {
                type Native = $native;
                type Value = $wrapper;

                const ID: ua::AttributeId = ua::AttributeId::$name;

                fn into_python(value: AttributeValue<Self::Value>) -> PyAttributeValue {
                    PyAttributeValue {
                        result: ResultValue::$name(value)
                    }
                }
            }
        )*

        #[derive(Clone)]
        enum ResultValue {
            $($name(AttributeValue<$wrapper>),)*
        }

        impl ResultValue {
            fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
                match self {
                    $(Self::$name(value) => value.value(py),)*
                }
            }

            fn metadata(&self) -> (ua::AttributeId, &ua::DataValue) {
                match self {
                    $(Self::$name(value) => (value.attribute_id, &value.native),)*
                }
            }
        }

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

        impl PyAttributeValue {
            pub(crate) fn from_native(id: ua::AttributeId, value: ua::DataValue) -> Self {
                match id {
                    $(ua::AttributeId::$name => $name::into_python($name::wrap(value)),)*
                }
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

/// An attribute-aware result with a deferred payload and original quality metadata.
///
/// Returned by Node reads and subscriptions; it is not a DataValue subclass and
/// provides no raw DataValue accessor. Missing payloads become ``None``. Bad or
/// Uncertain quality alone does not raise. Access ``value`` to decode the bounded
/// supported payload, retaining metadata if conversion fails.
#[pyclass(
    name = "AttributeValue",
    frozen,
    from_py_object,
    generic,
    module = "instro.unstable.opcua.types"
)]
#[derive(Clone)]
pub(crate) struct PyAttributeValue {
    result: ResultValue,
}

#[pymethods]
impl PyAttributeValue {
    /// object | None: The semantic payload, decoded on access.
    ///
    /// Raises:
    ///     OpcUaError: ``BadTypeMismatch`` for an incompatible fixed attribute
    ///         payload, or ``BadNotSupported`` for unsupported types or dimensions.
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.result.value(py)
    }

    /// AttributeId: The attribute associated with this result.
    #[getter]
    fn attribute_id(&self) -> AttributeId {
        <AttributeId as UaType>::new(self.result.metadata().0)
    }

    /// int | None: Original source fractional field, in units of 10 picoseconds.
    #[getter]
    fn source_picoseconds(&self) -> Option<u16> {
        self.result.metadata().1.source_picoseconds
    }

    /// int | None: Original server fractional field, in units of 10 picoseconds.
    #[getter]
    fn server_picoseconds(&self) -> Option<u16> {
        self.result.metadata().1.server_picoseconds
    }

    /// StatusCode: Original sample quality; defaults to Good when absent.
    #[getter]
    fn status(&self) -> StatusCode {
        let status = self
            .result
            .metadata()
            .1
            .status
            .unwrap_or(ua::StatusCode::Good);

        <StatusCode as UaType>::new(status)
    }

    /// DateTime | None: Native source timestamp, without the fractional field.
    #[getter]
    fn source_timestamp(&self) -> Option<DateTime> {
        self.result
            .metadata()
            .1
            .source_timestamp
            .map(<DateTime as UaType>::new)
    }

    /// DateTime | None: Native server timestamp, without the fractional field.
    #[getter]
    fn server_timestamp(&self) -> Option<DateTime> {
        self.result
            .metadata()
            .1
            .server_timestamp
            .map(<DateTime as UaType>::new)
    }

    /// int | None: Source Unix nanoseconds, including the truncated fractional field.
    #[getter]
    fn source_unix_timestamp(&self) -> Option<i128> {
        let native = self.result.metadata().1;
        unix_timestamp(native.source_timestamp, native.source_picoseconds)
    }

    /// int | None: Server Unix nanoseconds, including the truncated fractional field.
    #[getter]
    fn server_unix_timestamp(&self) -> Option<i128> {
        let native = self.result.metadata().1;
        unix_timestamp(native.server_timestamp, native.server_picoseconds)
    }
}

#[cfg(test)]
#[path = "attribute_tests.rs"]
mod tests;
