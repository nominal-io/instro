use opcua::types as ua;
use pyo3::IntoPyObjectExt as _;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use pyo3::types::PyDict;
use pyo3::types::PyList;

use super::UaType;
use super::wrappers::*;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PayloadError {
    TypeMismatch,
    NotSupported,
}

impl From<PayloadError> for PyErr {
    fn from(error: PayloadError) -> Self {
        let (code, message) = match error {
            PayloadError::TypeMismatch => (
                ua::StatusCode::BadTypeMismatch,
                "payload does not match the selected attribute",
            ),
            PayloadError::NotSupported => (
                ua::StatusCode::BadNotSupported,
                "payload type or dimensions are not supported",
            ),
        };

        crate::error::status(code, message)
    }
}

pub(crate) trait Payload: Clone {
    type Native: Clone;

    fn decode(value: &ua::Variant) -> Result<Self, PayloadError>;

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>>;
}

pub(crate) trait Scalar: UaType {
    const KIND: ua::VariantScalarTypeId;

    fn decode_native(value: &ua::Variant) -> Result<Self::Inner, PayloadError>;

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>>;
}

impl<T: Scalar> Payload for T {
    type Native = T::Inner;

    fn decode(value: &ua::Variant) -> Result<Self, PayloadError> {
        T::decode_native(value).map(<T as UaType>::new)
    }

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Scalar::to_python(self, py)
    }
}

impl<T: Scalar> Payload for Vec<T> {
    type Native = Vec<T::Inner>;

    fn decode(value: &ua::Variant) -> Result<Self, PayloadError> {
        let ua::Variant::Array(array) = value else {
            return Err(PayloadError::TypeMismatch);
        };

        if array.value_type != T::KIND {
            return Err(PayloadError::TypeMismatch);
        }

        let is_multidimensional = array
            .dimensions
            .as_ref()
            .is_some_and(|dimensions| dimensions.len() > 1);

        if is_multidimensional {
            return Err(PayloadError::NotSupported);
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

                fn decode_native(value: &ua::Variant) -> Result<Self, PayloadError> {
                    match value {
                        ua::Variant::$variant(value) => Ok(*value),
                        _ => Err(PayloadError::TypeMismatch),
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
    ($wrapper:ty, $kind:ident, $variant:ident, |$inner:ident| $native:expr) => {
        impl Scalar for $wrapper {
            const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::$kind;

            fn decode_native(value: &ua::Variant) -> Result<Self::Inner, PayloadError> {
                match value {
                    ua::Variant::$variant($inner) => Ok($native),
                    _ => Err(PayloadError::TypeMismatch),
                }
            }

            fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
                self.clone().into_py_any(py)
            }
        }
    };
}

native_scalar!(NodeId, NodeId, NodeId, |value| (**value).clone());

native_scalar!(ExpandedNodeId, ExpandedNodeId, ExpandedNodeId, |value| {
    (**value).clone()
});

native_scalar!(QualifiedName, QualifiedName, QualifiedName, |value| {
    (**value).clone()
});

native_scalar!(LocalizedText, LocalizedText, LocalizedText, |value| {
    (**value).clone()
});

native_scalar!(StatusCode, StatusCode, StatusCode, |value| *value);

macro_rules! flag_scalar {
    ($wrapper:ty, $native:ty, $variant:ident, $repr:ty) => {
        native_scalar!($wrapper, $variant, $variant, |value| {
            <$native>::from_bits_retain(*value as $repr)
        });
    };
}

flag_scalar!(AttributeWriteMask, ua::AttributeWriteMask, UInt32, i32);
flag_scalar!(AccessLevelType, ua::AccessLevelType, Byte, u8);
flag_scalar!(AccessLevelExType, ua::AccessLevelExType, UInt32, i32);
flag_scalar!(
    AccessRestrictionType,
    ua::AccessRestrictionType,
    UInt16,
    i16
);
flag_scalar!(EventNotifierType, ua::EventNotifierType, Byte, u8);

impl Scalar for NodeClass {
    const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::Int32;

    fn decode_native(value: &ua::Variant) -> Result<Self::Inner, PayloadError> {
        match value {
            ua::Variant::Int32(value) => {
                ua::NodeClass::try_from(*value).map_err(|_| PayloadError::TypeMismatch)
            }

            _ => Err(PayloadError::TypeMismatch),
        }
    }

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        (*self).into_py_any(py)
    }
}

macro_rules! representation {
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

            fn decode_native(value: &ua::Variant) -> Result<Self::Inner, PayloadError> {
                match value {
                    ua::Variant::$kind($value) => Ok($decode),
                    _ => Err(PayloadError::TypeMismatch),
                }
            }

            fn to_python(&$slf, $py: Python<'_>) -> PyResult<Py<PyAny>> {
                $convert
            }
        }
    };
}

representation!(
    Text,
    ua::UAString,
    String,
    |value| value.clone(),
    |self, py| self.0.as_ref().into_py_any(py)
);

representation!(
    Bytes,
    ua::ByteString,
    ByteString,
    |value| value.clone(),
    |self, py| Ok(PyBytes::new(py, self.0.as_ref()).into_any().unbind())
);

representation!(
    Xml,
    ua::XmlElement,
    XmlElement,
    |value| value.clone(),
    |self, py| self.0.to_string().into_py_any(py)
);

representation!(
    Timestamp,
    ua::DateTime,
    DateTime,
    |value| **value,
    |self, py| crate::time::unix_timestamp(Some(self.0), None).into_py_any(py)
);

representation!(
    Uuid,
    ua::Guid,
    Guid,
    |value| (**value).clone(),
    |self, py| uuid::Uuid::from(self.0.clone()).into_py_any(py)
);

representation!(SamplingInterval, f64, Double, |value| *value, |self, py| {
    let kwargs = PyDict::new(py);
    kwargs.set_item("milliseconds", self.0)?;

    Ok(py
        .import("datetime")?
        .getattr("timedelta")?
        .call((), Some(&kwargs))?
        .unbind())
});

macro_rules! structure_scalar {
    ($($wrapper:ident),* $(,)?) => {
        $(
            impl Scalar for $wrapper {
                const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::ExtensionObject;

                fn decode_native(value: &ua::Variant) -> Result<Self::Inner, PayloadError> {
                    let ua::Variant::ExtensionObject(object) = value else {
                        return Err(PayloadError::TypeMismatch);
                    };

                    let body = object.body.as_ref().ok_or(PayloadError::NotSupported)?;
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

            fn decode_native(value: &ua::Variant) -> Result<Self::Inner, PayloadError> {
                let ua::Variant::ExtensionObject(object) = value else {
                    return Err(PayloadError::TypeMismatch);
                };

                let body = object.body
                    .as_ref()
                    .ok_or(PayloadError::NotSupported)?
                    .as_dyn_any_ref();

                $(
                    if let Some(value) = body.downcast_ref::<ua::$wrapper>() {
                        return Ok(NativeStructure::$wrapper(value.clone()));
                    }
                )*

                Err(PayloadError::NotSupported)
            }

            fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
                match self.as_ua_ref() {
                    $(
                        NativeStructure::$wrapper(value) => <$wrapper as UaType>::new(value.clone())
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

fn incompatible_structure(object: &ua::ExtensionObject) -> PayloadError {
    let Some(body) = &object.body else {
        return PayloadError::NotSupported;
    };

    let body = body.as_dyn_any_ref();
    // Native fallback bodies retain encoded content without establishing its decoded schema.
    if body.is::<ua::ByteStringBody>() || body.is::<ua::JsonBody>() || body.is::<ua::XmlBody>() {
        PayloadError::NotSupported
    } else {
        PayloadError::TypeMismatch
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

    fn decode_native(value: &ua::Variant) -> Result<Self::Inner, PayloadError> {
        let ua::Variant::ExtensionObject(object) = value else {
            return Err(PayloadError::TypeMismatch);
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
                <StructureDefinition as UaType>::new(value.clone()).into_py_any(py)
            }

            ua::DataTypeDefinition::Enum(value) => {
                <EnumDefinition as UaType>::new(value.clone()).into_py_any(py)
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
    fn decode(value: &ua::Variant) -> Result<Self, PayloadError> {
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

            fn decode(value: &ua::Variant) -> Result<Self, PayloadError> {
                if let ua::Variant::Array(array) = value {
                    let is_multidimensional = array.dimensions
                        .as_ref()
                        .is_some_and(|dimensions| dimensions.len() > 1);

                    if is_multidimensional {
                        return Err(PayloadError::NotSupported);
                    }
                }

                let kind = match value {
                    ua::Variant::Array(array) => array.value_type,
                    value => value.scalar_type_id().ok_or(PayloadError::NotSupported)?,
                };

                match kind {
                    $(
                        ua::VariantScalarTypeId::$kind => ScalarOrList::<$type>::decode(value)
                            .map(Self::$kind),
                    )*
                    ua::VariantScalarTypeId::ExtensionObject =>
                        ScalarOrList::<SupportedStructure>::decode(value).map(Self::Structure),
                    _ => Err(PayloadError::NotSupported),
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
