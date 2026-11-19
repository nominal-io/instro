use opcua::types as ua;
use pyo3::IntoPyObjectExt as _;
use pyo3::exceptions::PyOverflowError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use pyo3::types::PyDict;
use pyo3::types::PyList;

use super::UaType;
use super::wrappers::*;

fn type_mismatch() -> PyErr {
    crate::error::status(
        ua::StatusCode::BadTypeMismatch,
        "payload does not match the selected attribute",
    )
}

fn not_supported() -> PyErr {
    crate::error::status(
        ua::StatusCode::BadNotSupported,
        "payload type or dimensions are not supported",
    )
}

pub(crate) trait Payload: Clone {
    type Native: Clone;

    fn decode(value: &ua::Variant) -> PyResult<Self>;

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>>;
}

pub(crate) trait Scalar: UaType {
    const KIND: ua::VariantScalarTypeId;

    fn decode_native(value: &ua::Variant) -> PyResult<Self::Inner>;

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>>;
}

impl<T: Scalar> Payload for T {
    type Native = T::Inner;

    fn decode(value: &ua::Variant) -> PyResult<Self> {
        T::decode_native(value).map(T::new)
    }

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Scalar::to_python(self, py)
    }
}

impl<T: Scalar> Payload for Vec<T> {
    type Native = Vec<T::Inner>;

    fn decode(value: &ua::Variant) -> PyResult<Self> {
        let ua::Variant::Array(array) = value else {
            return Err(type_mismatch());
        };

        if array.value_type != T::KIND {
            return Err(type_mismatch());
        }

        let is_multidimensional = array
            .dimensions
            .as_ref()
            .is_some_and(|dimensions| dimensions.len() > 1);

        if is_multidimensional {
            return Err(not_supported());
        }

        array.values.iter().map(T::decode).collect()
    }

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let values = self
            .iter()
            .map(|value| Scalar::to_python(value, py))
            .collect::<PyResult<Vec<_>>>()?;

        Ok(PyList::new(py, values)?.into_any().unbind())
    }
}

macro_rules! primitive {
    ($($type:ty => $variant:ident),* $(,)?) => {
        $(
            impl UaType for $type {
                type Inner = Self;

                fn new(value: Self) -> Self {
                    value
                }

                fn as_ua_ref(&self) -> &Self {
                    self
                }
            }

            impl Scalar for $type {
                const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::$variant;

                fn decode_native(value: &ua::Variant) -> PyResult<Self> {
                    match value {
                        ua::Variant::$variant(value) => Ok(*value),
                        _ => Err(type_mismatch()),
                    }
                }

                fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
                    self.as_ua_ref().into_py_any(py)
                }
            }
        )*
    };
}

primitive!(
    bool => Boolean,
    i8 => SByte,
    u8 => Byte,
    i16 => Int16,
    u16 => UInt16,
    i32 => Int32,
    u32 => UInt32,
    i64 => Int64,
    u64 => UInt64,
    f32 => Float,
    f64 => Double
);

macro_rules! native_scalar {
    ($name:ident, |$value:ident| $decode:expr) => {
        native_scalar!($name, $name, |$value| $decode);
    };

    ($wrapper:ty, $variant:ident, |$value:ident| $decode:expr) => {
        impl Scalar for $wrapper {
            const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::$variant;

            fn decode_native(value: &ua::Variant) -> PyResult<Self::Inner> {
                match value {
                    ua::Variant::$variant($value) => Ok($decode),
                    _ => Err(type_mismatch()),
                }
            }

            fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
                self.clone().into_py_any(py)
            }
        }
    };
}

native_scalar!(NodeId, |value| (**value).clone());
native_scalar!(ExpandedNodeId, |value| (**value).clone());
native_scalar!(QualifiedName, |value| (**value).clone());
native_scalar!(LocalizedText, |value| (**value).clone());
native_scalar!(StatusCode, |value| *value);

macro_rules! flag_scalar {
    ($($wrapper:ident => $variant:ident as $repr:ty),* $(,)?) => {
        $(
            native_scalar!($wrapper, $variant, |value| {
                ua::$wrapper::from_bits_retain(*value as $repr)
            });
        )*
    };
}

flag_scalar!(
    AttributeWriteMask => UInt32 as i32,
    AccessLevelType => Byte as u8,
    AccessLevelExType => UInt32 as i32,
    AccessRestrictionType => UInt16 as i16,
    EventNotifierType => Byte as u8,
);

impl Scalar for NodeClass {
    const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::Int32;

    fn decode_native(value: &ua::Variant) -> PyResult<Self::Inner> {
        match value {
            ua::Variant::Int32(value) => {
                ua::NodeClass::try_from(*value).map_err(|_| type_mismatch())
            }

            _ => Err(type_mismatch()),
        }
    }

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        (*self).into_py_any(py)
    }
}

macro_rules! repr {
    (
        $name:ident,
        $native:ty,
        $kind:ident,
        |$value:ident| $decode:expr,
        |$slf:ident, $py:ident| $convert:expr
    ) => {
        #[derive(Clone)]
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

        impl Scalar for $name {
            const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::$kind;

            fn decode_native(value: &ua::Variant) -> PyResult<Self::Inner> {
                match value {
                    ua::Variant::$kind($value) => Ok($decode),
                    _ => Err(type_mismatch()),
                }
            }

            fn to_python(&$slf, $py: Python<'_>) -> PyResult<Py<PyAny>> {
                $convert
            }
        }
    };
}

repr!(
    Text,
    ua::UAString,
    String,
    |value| value.clone(),
    |self, py| self.0.as_ref().into_py_any(py)
);

repr!(
    Bytes,
    ua::ByteString,
    ByteString,
    |value| value.clone(),
    |self, py| Ok(PyBytes::new(py, self.0.as_ref()).into_any().unbind())
);

repr!(
    Xml,
    ua::XmlElement,
    XmlElement,
    |value| value.clone(),
    |self, py| self.0.to_string().into_py_any(py)
);

repr!(
    Timestamp,
    ua::DateTime,
    DateTime,
    |value| **value,
    |self, py| crate::time::unix_timestamp(Some(self.0), None).into_py_any(py)
);

repr!(
    Uuid,
    ua::Guid,
    Guid,
    |value| (**value).clone(),
    |self, py| uuid::Uuid::from(self.0.clone()).into_py_any(py)
);

repr!(SamplingInterval, f64, Double, |value| *value, |self, py| {
    let kwargs = PyDict::new(py);
    kwargs.set_item("milliseconds", self.0)?;

    Ok(py
        .import("datetime")?
        .getattr("timedelta")?
        .call((), Some(&kwargs))
        .map_err(|error| {
            if error.is_instance_of::<PyValueError>(py)
                || error.is_instance_of::<PyOverflowError>(py)
            {
                crate::error::status(
                    ua::StatusCode::BadOutOfRange,
                    "sampling interval is outside the datetime.timedelta range",
                )
            } else {
                error
            }
        })?
        .unbind())
});

macro_rules! structure_scalar {
    ($($wrapper:ident),* $(,)?) => {
        $(
            impl Scalar for $wrapper {
                const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::ExtensionObject;

                fn decode_native(value: &ua::Variant) -> PyResult<Self::Inner> {
                    let ua::Variant::ExtensionObject(object) = value else {
                        return Err(type_mismatch());
                    };

                    let body = object.body.as_ref().ok_or_else(not_supported)?;
                    body.as_dyn_any_ref()
                        .downcast_ref::<ua::$wrapper>()
                        .cloned()
                        .ok_or_else(|| {
                            incompatible_structure(object)
                        })
                }

                fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
                    self.clone().into_py_any(py)
                }
            }
        )*

        #[derive(Clone)]
        pub(crate) enum NativeStructure {
            $($wrapper(ua::$wrapper),)*
        }

        #[derive(Clone)]
        pub(crate) struct SupportedStructure(NativeStructure);

        impl UaType for SupportedStructure {
            type Inner = NativeStructure;

            fn new(value: Self::Inner) -> Self {
                Self(value)
            }

            fn as_ua_ref(&self) -> &Self::Inner {
                &self.0
            }
        }

        impl Scalar for SupportedStructure {
            const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::ExtensionObject;

            fn decode_native(value: &ua::Variant) -> PyResult<Self::Inner> {
                let ua::Variant::ExtensionObject(object) = value else {
                    return Err(type_mismatch());
                };

                let body = object.body
                    .as_ref()
                    .ok_or_else(not_supported)?
                    .as_dyn_any_ref();

                $(
                    if let Some(value) = body.downcast_ref::<ua::$wrapper>() {
                        return Ok(NativeStructure::$wrapper(value.clone()));
                    }
                )*

                Err(not_supported())
            }

            fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
                match self.as_ua_ref() {
                    $(
                        NativeStructure::$wrapper(value) => <$wrapper>::new(value.clone())
                            .into_py_any(py),
                    )*
                }
            }
        }
    };
}

structure_scalar!(
    RolePermissionType,
    StructureDefinition,
    StructureField,
    EnumDefinition,
    EnumField
);

fn incompatible_structure(object: &ua::ExtensionObject) -> PyErr {
    let Some(body) = &object.body else {
        return not_supported();
    };

    let body = body.as_dyn_any_ref();
    // Native fallback bodies retain encoded content without establishing its decoded schema.
    if body.is::<ua::ByteStringBody>() || body.is::<ua::JsonBody>() || body.is::<ua::XmlBody>() {
        not_supported()
    } else {
        type_mismatch()
    }
}

#[derive(Clone)]
pub(crate) struct Definition(ua::DataTypeDefinition);

impl UaType for Definition {
    type Inner = ua::DataTypeDefinition;

    fn new(value: Self::Inner) -> Self {
        Self(value)
    }

    fn as_ua_ref(&self) -> &Self::Inner {
        &self.0
    }
}

impl Scalar for Definition {
    const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::ExtensionObject;

    fn decode_native(value: &ua::Variant) -> PyResult<Self::Inner> {
        let ua::Variant::ExtensionObject(object) = value else {
            return Err(type_mismatch());
        };

        if let Some(body) = &object.body {
            let body = body.as_dyn_any_ref();
            if let Some(value) = body.downcast_ref::<ua::StructureDefinition>() {
                return Ok(ua::DataTypeDefinition::Structure(value.clone()));
            }

            if let Some(value) = body.downcast_ref::<ua::EnumDefinition>() {
                return Ok(ua::DataTypeDefinition::Enum(value.clone()));
            }
        }

        Err(incompatible_structure(object))
    }

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.0 {
            ua::DataTypeDefinition::Structure(value) => {
                StructureDefinition::new(value.clone()).into_py_any(py)
            }

            ua::DataTypeDefinition::Enum(value) => {
                EnumDefinition::new(value.clone()).into_py_any(py)
            }
        }
    }
}

#[derive(Clone)]
pub(crate) enum ScalarOrList<T> {
    Scalar(T),
    List(Vec<T>),
}

impl<T: Scalar> ScalarOrList<T> {
    fn decode(value: &ua::Variant) -> PyResult<Self> {
        match value {
            ua::Variant::Array(_) => Vec::<T>::decode(value).map(Self::List),
            _ => T::decode(value).map(Self::Scalar),
        }
    }

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match self {
            Self::Scalar(value) => Scalar::to_python(value, py),
            Self::List(value) => Payload::to_python(value, py),
        }
    }
}

macro_rules! bounded_values {
    ($($kind:ident => $type:ty),* $(,)?) => {
        #[derive(Clone)]
        pub(crate) enum BoundedValue {
            $($kind(ScalarOrList<$type>),)*
            Structure(ScalarOrList<SupportedStructure>),
        }

        impl Payload for BoundedValue {
            type Native = Self;

            fn decode(value: &ua::Variant) -> PyResult<Self> {
                if let ua::Variant::Array(array) = value {
                    let is_multidimensional = array.dimensions
                        .as_ref()
                        .is_some_and(|dimensions| dimensions.len() > 1);

                    if is_multidimensional {
                        return Err(not_supported());
                    }
                }

                let kind = match value {
                    ua::Variant::Array(array) => array.value_type,
                    value => value.scalar_type_id().ok_or_else(not_supported)?,
                };

                match kind {
                    $(
                        ua::VariantScalarTypeId::$kind => ScalarOrList::<$type>::decode(value)
                            .map(Self::$kind),
                    )*
                    ua::VariantScalarTypeId::ExtensionObject =>
                        ScalarOrList::<SupportedStructure>::decode(value).map(Self::Structure),
                    _ => Err(not_supported()),
                }
            }

            fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
                match self {
                    $(Self::$kind(value) => value.to_python(py),)*
                    Self::Structure(value) => value.to_python(py),
                }
            }
        }
    };
}

bounded_values!(
    Boolean => bool,
    SByte => i8,
    Byte => u8,
    Int16 => i16,
    UInt16 => u16,
    Int32 => i32,
    UInt32 => u32,
    Int64 => i64,
    UInt64 => u64,
    Float => f32,
    Double => f64,

    String => Text,
    DateTime => Timestamp,
    Guid => Uuid,
    ByteString => Bytes,
    XmlElement => Xml,

    NodeId => NodeId,
    ExpandedNodeId => ExpandedNodeId,
    StatusCode => StatusCode,
    QualifiedName => QualifiedName,
    LocalizedText => LocalizedText
);

#[cfg(test)]
#[allow(clippy::expect_used)]
mod decoding_boundary_tests {
    use ua::StatusCode as Code;

    use super::*;
    use crate::error::failure_status;

    fn array(
        kind: ua::VariantScalarTypeId,
        values: Vec<ua::Variant>,
        dimensions: Option<Vec<u32>>,
    ) -> ua::Variant {
        ua::Variant::Array(Box::new(ua::Array {
            value_type: kind,
            values,
            dimensions,
        }))
    }

    #[test]
    fn sampling_interval_reports_unrepresentable_values() {
        Python::initialize();
        Python::attach(|py| {
            for milliseconds in [f64::NAN, f64::INFINITY, 1e20] {
                let interval = SamplingInterval::new(milliseconds);
                assert_eq!(
                    failure_status(Scalar::to_python(&interval, py)),
                    Code::BadOutOfRange
                );
            }
        });
    }

    #[test]
    fn fixed_scalar_and_list_decoders_enforce_declared_shape_and_elements() -> PyResult<()> {
        use ua::VariantScalarTypeId::Int32;
        use ua::VariantScalarTypeId::UInt32;

        Python::initialize();
        assert_eq!(
            failure_status(i32::decode(&ua::Variant::Double(42.0))),
            Code::BadTypeMismatch
        );

        assert_eq!(
            failure_status(Vec::<u32>::decode(&ua::Variant::UInt32(7))),
            Code::BadTypeMismatch
        );

        let cases = [
            (Int32, vec![], None, Code::BadTypeMismatch),
            (
                UInt32,
                vec![ua::Variant::Int32(7)],
                None,
                Code::BadTypeMismatch,
            ),
            (
                UInt32,
                vec![ua::Variant::UInt32(7)],
                Some(vec![1, 1]),
                Code::BadNotSupported,
            ),
        ];

        for (kind, values, dimensions, expected) in cases {
            let value = array(kind, values, dimensions);
            assert_eq!(failure_status(Vec::<u32>::decode(&value)), expected);
        }

        let empty = array(UInt32, vec![], None);
        assert_eq!(Vec::<u32>::decode(&empty)?, Vec::<u32>::new());
        Ok(())
    }

    #[test]
    fn bounded_payload_rejects_unsupported_shapes_and_fixed_roles_reject_mixed_structures() {
        Python::initialize();
        let unsupported = [
            ua::Variant::Empty,
            ua::Variant::Variant(Box::new(ua::Variant::Int32(1))),
            ua::Variant::ExtensionObject(ua::ExtensionObject::null()),
            array(ua::VariantScalarTypeId::UInt32, vec![], Some(vec![0, 0])),
        ];

        for value in unsupported {
            assert_eq!(
                failure_status(BoundedValue::decode(&value)),
                Code::BadNotSupported
            );
        }

        let role = ua::ExtensionObject::new(ua::RolePermissionType::default());
        let field = ua::ExtensionObject::new(ua::EnumField::default());
        let mixed = array(
            ua::VariantScalarTypeId::ExtensionObject,
            vec![
                ua::Variant::ExtensionObject(role),
                ua::Variant::ExtensionObject(field),
            ],
            None,
        );

        assert_eq!(
            failure_status(Vec::<RolePermissionType>::decode(&mixed)),
            Code::BadTypeMismatch
        );
    }
}
