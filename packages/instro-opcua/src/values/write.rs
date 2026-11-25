use opcua::types as ua;
use pyo3::conversion::FromPyObjectOwned;
use pyo3::exceptions::PyTypeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBool;
use pyo3::types::PyBytes;
use pyo3::types::PyFloat;
use pyo3::types::PyInt;
use pyo3::types::PyList;
use pyo3::types::PySequence;
use pyo3::types::PyString;
use pyo3::types::PyTuple;

use super::EnumField;
use super::ExpandedNodeId;
use super::LocalizedText;
use super::NodeId;
use super::QualifiedName;
use super::StatusCode;
use super::XmlElement;
use super::payload::ReadValue;
use crate::config::extract_integer;

pub(crate) fn data_type(value: &Bound<'_, PyAny>) -> PyResult<ua::DataTypeId> {
    let class = value
        .py()
        .import("instro.unstable.opcua.types")?
        .getattr("DataType")?;
    if !value.is_instance(&class)? {
        return Err(PyTypeError::new_err("dtype must be a DataType"));
    }

    ua::DataTypeId::try_from(value.extract::<u32>()?)
        .map_err(|_| PyValueError::new_err("unsupported write data type"))
}

fn array_items<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Option<&'a Bound<'py, PySequence>> {
    if let Ok(list) = value.cast::<PyList>() {
        return Some(list.as_sequence());
    }

    value
        .cast::<PyTuple>()
        .ok()
        .map(|tuple| tuple.as_sequence())
}

fn default_type(value: &Bound<'_, PyAny>) -> PyResult<ua::DataTypeId> {
    use ua::DataTypeId as D;

    let item = if let Some(items) = array_items(value) {
        items.try_iter()?.next().transpose()?.ok_or_else(|| {
            PyValueError::new_err("an empty list or tuple requires an explicit dtype")
        })?
    } else {
        value.clone()
    };

    let py = item.py();
    let dtype = match &item {
        v if v.is_instance_of::<PyBool>() => D::Boolean,
        v if v.is_exact_instance_of::<PyInt>() => D::Int64,
        v if v.is_instance_of::<PyFloat>() => D::Double,
        v if v.is_instance_of::<PyString>() => D::String,
        v if v.is_instance_of::<PyBytes>() => D::ByteString,
        v if v.is_instance_of::<XmlElement>() => D::XmlElement,
        v if v.is_instance_of::<NodeId>() => D::NodeId,
        v if v.is_instance_of::<ExpandedNodeId>() => D::ExpandedNodeId,
        v if v.is_instance_of::<StatusCode>() => D::StatusCode,
        v if v.is_instance_of::<QualifiedName>() => D::QualifiedName,
        v if v.is_instance_of::<LocalizedText>() => D::LocalizedText,
        v if v.is_instance_of::<EnumField>() => D::EnumField,
        v if v.is_instance_of::<PyInt>()
            && v.is_instance(&py.import("enum")?.getattr("IntEnum")?)? =>
        {
            D::Int32
        }
        v if v.is_instance_of::<PyInt>() => D::Int64,
        v if v.is_instance(&py.import("uuid")?.getattr("UUID")?)? => D::Guid,
        _ => {
            return Err(PyTypeError::new_err(
                "expected a writable scalar or a list or tuple of writable scalars",
            ));
        }
    };

    Ok(dtype)
}

pub(crate) fn encode(
    value: &Bound<'_, PyAny>,
    dtype: Option<ua::DataTypeId>,
) -> PyResult<ua::Variant> {
    use ua::DataTypeId as D;
    use ua::Variant as V;
    use ua::VariantScalarTypeId as K;

    let dtype = match dtype {
        Some(dtype) => dtype,
        None => default_type(value)?,
    };

    match dtype {
        D::Boolean => scalar_or_array(value, K::Boolean, |item| Ok(V::Boolean(item.extract()?))),
        D::SByte => integer(value, K::SByte, V::SByte),
        D::Byte => integer(value, K::Byte, V::Byte),
        D::Int16 => integer(value, K::Int16, V::Int16),
        D::UInt16 => integer(value, K::UInt16, V::UInt16),
        D::Int32 => integer(value, K::Int32, V::Int32),
        D::UInt32 => integer(value, K::UInt32, V::UInt32),
        D::Int64 => integer(value, K::Int64, V::Int64),
        D::UInt64 => integer(value, K::UInt64, V::UInt64),
        D::XmlElement => wrapper::<XmlElement>(value),
        D::NodeId => wrapper::<NodeId>(value),
        D::ExpandedNodeId => wrapper::<ExpandedNodeId>(value),
        D::StatusCode => wrapper::<StatusCode>(value),
        D::QualifiedName => wrapper::<QualifiedName>(value),
        D::LocalizedText => wrapper::<LocalizedText>(value),
        D::EnumField => wrapper::<EnumField>(value),

        D::Double => scalar_or_array(value, K::Double, |item| Ok(V::Double(double(item)?))),
        D::String => scalar_or_array(value, K::String, |item| {
            Ok(V::String(item.extract::<String>()?.into()))
        }),
        D::ByteString => scalar_or_array(value, K::ByteString, |item| {
            let bytes = item.cast::<PyBytes>()?.as_bytes().to_vec();
            Ok(V::ByteString(bytes.into()))
        }),
        D::DateTime => scalar_or_array(value, K::DateTime, |item| {
            let nanos = extract_integer(item)?;
            let value = crate::time::datetime_from_unix_nanos(nanos)?;
            Ok(V::DateTime(Box::new(value)))
        }),
        D::Guid => scalar_or_array(value, K::Guid, |item| {
            Ok(V::Guid(Box::new(item.extract::<uuid::Uuid>()?.into())))
        }),
        D::Float => scalar_or_array(value, K::Float, |item| {
            let wide = double(item)?;
            let narrow = wide as f32;
            if wide.is_finite() && !narrow.is_finite() {
                return Err(PyValueError::new_err(
                    "value exceeds the OPC UA Float range",
                ));
            }

            Ok(V::Float(narrow))
        }),
        _ => Err(PyValueError::new_err(format!(
            "unsupported write data type: {dtype:?}"
        ))),
    }
}

fn wrapper<T>(value: &Bound<'_, PyAny>) -> PyResult<ua::Variant>
where
    T: for<'py> FromPyObjectOwned<'py> + ReadValue + ua::IntoVariant,
{
    scalar_or_array(value, T::KIND, |item| {
        item.extract::<T>()
            .map(ua::Variant::from)
            .map_err(Into::into)
    })
}

fn integer<T>(
    value: &Bound<'_, PyAny>,
    kind: ua::VariantScalarTypeId,
    variant: impl Fn(T) -> ua::Variant,
) -> PyResult<ua::Variant>
where
    T: for<'py> FromPyObjectOwned<'py>,
{
    scalar_or_array(value, kind, |item| Ok(variant(extract_integer(item)?)))
}

fn double(value: &Bound<'_, PyAny>) -> PyResult<f64> {
    if value.is_instance_of::<PyBool>() {
        return Err(PyTypeError::new_err("expected a real number"));
    }

    value.extract()
}

fn scalar_or_array(
    value: &Bound<'_, PyAny>,
    kind: ua::VariantScalarTypeId,
    encode: impl Fn(&Bound<'_, PyAny>) -> PyResult<ua::Variant>,
) -> PyResult<ua::Variant> {
    let Some(items) = array_items(value) else {
        return encode(value);
    };

    let values = items
        .try_iter()?
        .map(|item| encode(&item?))
        .collect::<PyResult<_>>()?;

    Ok(ua::Variant::Array(Box::new(ua::Array {
        value_type: kind,
        values,
        dimensions: None,
    })))
}
