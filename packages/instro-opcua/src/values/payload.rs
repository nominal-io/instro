use std::any::TypeId;

use opcua::types as ua;
use pyo3::IntoPyObjectExt as _;
use pyo3::exceptions::PyOverflowError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use pyo3::types::PyDict;
use pyo3::types::PyList;
use pyo3::types::PyNone;

use super::wrappers::*;
use crate::error;

pub(super) fn type_mismatch() -> PyErr {
    error::status(
        ua::StatusCode::BadTypeMismatch,
        "payload does not match the selected attribute",
    )
}

fn not_supported() -> PyErr {
    error::status(
        ua::StatusCode::BadNotSupported,
        "payload type or dimensions are not supported",
    )
}

pub(super) trait ReadValue {
    const KIND: ua::VariantScalarTypeId;

    fn read(py: Python<'_>, value: &ua::Variant) -> PyResult<Py<PyAny>>;
}

pub(super) fn read_repr<T: ReadValue + ua::TryFromVariant>(value: &ua::Variant) -> PyResult<T> {
    if value.type_id() != ua::VariantTypeId::Scalar(T::KIND) {
        return Err(type_mismatch());
    }

    T::try_from_variant(value.clone()).map_err(|_| type_mismatch())
}

type Reader = fn(Python<'_>, &ua::Variant) -> PyResult<Py<PyAny>>;

fn read_array(py: Python<'_>, array: &ua::Array, read_element: Reader) -> PyResult<Py<PyAny>> {
    let is_multidimensional = array
        .dimensions
        .as_ref()
        .is_some_and(|dimensions| dimensions.len() > 1);

    if is_multidimensional {
        return Err(not_supported());
    }

    let list = PyList::empty(py);
    for value in &array.values {
        list.append(read_element(py, value)?)?;
    }

    Ok(list.into_any().unbind())
}

pub(super) fn read_list<T: ReadValue>(py: Python<'_>, value: &ua::Variant) -> PyResult<Py<PyAny>> {
    let ua::Variant::Array(array) = value else {
        return Err(type_mismatch());
    };

    if array.value_type != T::KIND {
        return Err(type_mismatch());
    }

    read_array(py, array, T::read)
}

fn py_none(py: Python<'_>) -> Py<PyAny> {
    PyNone::get(py).as_any().clone().unbind()
}

impl ReadValue for ua::UAString {
    const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::String;

    fn read(py: Python<'_>, maybe_str: &ua::Variant) -> PyResult<Py<PyAny>> {
        let ua::Variant::String(str) = maybe_str else {
            return Err(type_mismatch());
        };

        match str.value() {
            Some(str) => str.into_py_any(py),
            _ => Ok(py_none(py)),
        }
    }
}

impl ReadValue for ua::ByteString {
    const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::ByteString;

    fn read(py: Python<'_>, maybe_bytes: &ua::Variant) -> PyResult<Py<PyAny>> {
        let ua::Variant::ByteString(bytes) = maybe_bytes else {
            return Err(type_mismatch());
        };

        match &bytes.value {
            Some(bytes) => PyBytes::new(py, bytes.as_ref()).into_py_any(py),
            None => Ok(py_none(py)),
        }
    }
}

impl ReadValue for ua::DateTime {
    const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::DateTime;

    fn read(py: Python<'_>, maybe_dt: &ua::Variant) -> PyResult<Py<PyAny>> {
        let ua::Variant::DateTime(dt) = maybe_dt else {
            return Err(type_mismatch());
        };

        crate::time::unix_timestamp(Some(**dt), None).into_py_any(py)
    }
}

impl ReadValue for ua::Guid {
    const KIND: ua::VariantScalarTypeId = ua::VariantScalarTypeId::Guid;

    fn read(py: Python<'_>, value: &ua::Variant) -> PyResult<Py<PyAny>> {
        let ua::Variant::Guid(value) = value else {
            return Err(type_mismatch());
        };

        uuid::Uuid::from((**value).clone()).into_py_any(py)
    }
}

pub(super) fn read_sampling_interval(py: Python<'_>, value: &ua::Variant) -> PyResult<Py<PyAny>> {
    let ua::Variant::Double(milliseconds) = value else {
        return Err(type_mismatch());
    };

    let kwargs = PyDict::new(py);
    kwargs.set_item("milliseconds", milliseconds)?;
    let timedelta = py.import("datetime")?.getattr("timedelta")?;

    timedelta
        .call((), Some(&kwargs))
        .map(Bound::unbind)
        .map_err(|error| {
            if error.is_instance_of::<PyValueError>(py)
                || error.is_instance_of::<PyOverflowError>(py)
            {
                error::status(
                    ua::StatusCode::BadOutOfRange,
                    "sampling interval is outside the datetime.timedelta range",
                )
            } else {
                error
            }
        })
}

fn incompatible_body(body_type: TypeId) -> PyErr {
    // Fallback bodies retain encoded content without establishing its decoded schema.
    let encoded = [
        TypeId::of::<ua::ByteStringBody>(),
        TypeId::of::<ua::JsonBody>(),
        TypeId::of::<ua::XmlBody>(),
    ];

    if encoded.contains(&body_type) {
        not_supported()
    } else {
        type_mismatch()
    }
}

pub(super) fn incompatible_structure(object: &ua::ExtensionObject) -> PyErr {
    object
        .type_id()
        .map_or_else(not_supported, incompatible_body)
}

macro_rules! structure_family {
    (definitions: [$($definition:ident),+], others: [$($other:ident),+]) => {
        structure_family!(read_definition, incompatible_body, [$($definition),+]);
        structure_family!(read_structure, |_| not_supported(), [$($definition),+, $($other),+]);
    };

    ($read:ident, $unrecognized:expr, [$($wrapper:ident),+ $(,)?]) => {
        pub(super) fn $read(py: Python<'_>, value: &ua::Variant) -> PyResult<Py<PyAny>> {
            let ua::Variant::ExtensionObject(object) = value else {
                return Err(type_mismatch());
            };

            let body_type = object.type_id().ok_or_else(not_supported)?;

            $(
                if body_type == TypeId::of::<ua::$wrapper>() {
                    return $wrapper::try_from(object).and_then(|wrapper| wrapper.into_py_any(py));
                }
            )+

            Err(($unrecognized)(body_type))
        }
    };
}

structure_family! {
    definitions: [StructureDefinition, EnumDefinition],
    others: [RolePermissionType, StructureField, EnumField]
}

macro_rules! value_readers {
    (primitives: [$($primitive:ty => $kind:ident),+ $(,)?], scalars: [$($type:ty),+ $(,)?]) => {
        $(
            impl ReadValue for $primitive {
                const KIND: ua::VariantScalarTypeId =  ua::VariantScalarTypeId::$kind;

                fn read(py: Python<'_>,value: &ua::Variant) -> PyResult<Py<PyAny>>{
                    let ua::Variant::$kind(value) = value else {
                        return Err(type_mismatch());
                    };

                    value.into_py_any(py)
                }
            }
        )+

        pub(super) fn read_value(py: Python<'_>, value: &ua::Variant) -> PyResult<Py<PyAny>> {
            let kind = value.scalar_type_id().ok_or_else(not_supported)?;

            let read: Reader = match kind {
                $(ua::VariantScalarTypeId::$kind => <$primitive>::read,)+
                $(<$type>::KIND => <$type>::read,)+
                ua::VariantScalarTypeId::ExtensionObject => read_structure,
                _ => return Err(not_supported()),
            };

            match value {
                ua::Variant::Array(array) => read_array(py, array, read),
                value => read(py, value),
            }
        }
    };
}

value_readers! {
    primitives: [
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
        f64 => Double,
    ],
    scalars: [
        ua::UAString, ua::DateTime, ua::Guid, ua::ByteString, XmlElement,
        NodeId, ExpandedNodeId, StatusCode, QualifiedName, LocalizedText,
    ]
}

#[cfg(test)]
mod decoding_boundary_tests {
    use ua::StatusCode as Code;

    use super::*;
    use crate::error::failure_status;
    use crate::values::PyAttributeValue;

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
    fn value_and_data_type_definition_expose_the_decoded_enum_fields() -> PyResult<()> {
        let field = ua::EnumField {
            value: 37,
            ..Default::default()
        };

        let definition = ua::EnumDefinition {
            fields: Some(vec![field]),
        };
        let object = ua::ExtensionObject::new(definition);
        let native = ua::DataValue::from(ua::Variant::ExtensionObject(object));

        Python::initialize();
        Python::attach(|py| {
            for id in [ua::AttributeId::Value, ua::AttributeId::DataTypeDefinition] {
                let attribute = PyAttributeValue::from_native(py, id, native.clone());
                let attribute = Bound::new(py, attribute)?;
                let value = attribute.getattr("value")?;
                let field = value.getattr("fields")?.get_item(0)?;
                assert!(value.is_instance_of::<EnumDefinition>());
                assert_eq!(field.getattr("value")?.extract::<i64>()?, 37);
            }

            Ok(())
        })
    }

    #[test]
    fn sampling_interval_reports_unrepresentable_values() {
        Python::initialize();
        Python::attach(|py| {
            for milliseconds in [f64::NAN, f64::INFINITY, 1e20] {
                let interval = ua::Variant::Double(milliseconds);
                assert_eq!(
                    failure_status(read_sampling_interval(py, &interval)),
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
        Python::attach(|py| {
            assert_eq!(
                failure_status(i32::read(py, &ua::Variant::Double(42.0))),
                Code::BadTypeMismatch
            );

            assert_eq!(
                failure_status(read_list::<u32>(py, &ua::Variant::UInt32(7))),
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
                assert_eq!(failure_status(read_list::<u32>(py, &value)), expected);
            }

            let empty = array(UInt32, vec![], None);
            let result = read_list::<u32>(py, &empty)?;
            assert!(result.bind(py).cast::<PyList>()?.is_empty());
            Ok(())
        })
    }

    #[test]
    fn open_value_rejects_unsupported_shapes_and_fixed_roles_reject_mixed_structures() {
        Python::initialize();
        Python::attach(|py| {
            let unsupported = [
                ua::Variant::Empty,
                ua::Variant::Variant(Box::new(ua::Variant::Int32(1))),
                ua::Variant::ExtensionObject(ua::ExtensionObject::null()),
                array(ua::VariantScalarTypeId::UInt32, vec![], Some(vec![0, 0])),
            ];

            for value in unsupported {
                assert_eq!(
                    failure_status(read_value(py, &value)),
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
                failure_status(read_list::<RolePermissionType>(py, &mixed)),
                Code::BadTypeMismatch
            );
        });
    }
}
