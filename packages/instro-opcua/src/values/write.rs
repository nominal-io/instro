use opcua::types as ua;
use pyo3::exceptions::PyTypeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBool;
use pyo3::types::PyBytes;
use pyo3::types::PyList;

use super::ExpandedNodeId;
use super::LocalizedText;
use super::NodeId;
use super::QualifiedName;
use super::StatusCode;
use super::UaType;
use super::XmlElement;
use crate::config::NativeInteger;

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

pub(crate) fn encode(value: &Bound<'_, PyAny>, dtype: ua::DataTypeId) -> PyResult<ua::Variant> {
    use ua::DataTypeId as D;
    use ua::Variant as V;
    use ua::VariantScalarTypeId as K;

    match dtype {
        D::Boolean => scalar_or_list(value, K::Boolean, |item| Ok(V::Boolean(item.extract()?))),
        D::SByte => integer(value, K::SByte, V::SByte),
        D::Byte => integer(value, K::Byte, V::Byte),
        D::Int16 => integer(value, K::Int16, V::Int16),
        D::UInt16 => integer(value, K::UInt16, V::UInt16),
        D::Int32 => integer(value, K::Int32, V::Int32),
        D::UInt32 => integer(value, K::UInt32, V::UInt32),
        D::Int64 => integer(value, K::Int64, V::Int64),
        D::UInt64 => integer(value, K::UInt64, V::UInt64),
        D::Double | D::Duration => {
            scalar_or_list(value, K::Double, |item| Ok(V::Double(double(item)?)))
        }
        D::String => scalar_or_list(value, K::String, |item| {
            Ok(V::String(item.extract::<String>()?.into()))
        }),
        D::ByteString => scalar_or_list(value, K::ByteString, |item| {
            let bytes = item.cast::<PyBytes>()?.as_bytes().to_vec();
            Ok(V::ByteString(bytes.into()))
        }),
        D::DateTime | D::UtcTime => scalar_or_list(value, K::DateTime, |item| {
            let NativeInteger(nanos) = item.extract::<NativeInteger<i128>>()?;
            let value = crate::time::datetime_from_unix_nanos(nanos)?;
            Ok(V::DateTime(Box::new(value)))
        }),
        D::Guid => scalar_or_list(value, K::Guid, |item| {
            Ok(V::Guid(Box::new(item.extract::<uuid::Uuid>()?.into())))
        }),
        D::XmlElement => scalar_or_list(value, K::XmlElement, |item| {
            let xml = item.extract::<XmlElement>()?;
            Ok(V::XmlElement(xml.as_ua_ref().clone()))
        }),
        D::NodeId => scalar_or_list(value, K::NodeId, |item| {
            let id = item.extract::<NodeId>()?;
            Ok(V::NodeId(Box::new(id.as_ua_ref().clone())))
        }),
        D::ExpandedNodeId => scalar_or_list(value, K::ExpandedNodeId, |item| {
            let id = item.extract::<ExpandedNodeId>()?;
            Ok(V::ExpandedNodeId(Box::new(id.as_ua_ref().clone())))
        }),
        D::StatusCode => scalar_or_list(value, K::StatusCode, |item| {
            let status = item.extract::<StatusCode>()?;
            Ok(V::StatusCode(*status.as_ua_ref()))
        }),
        D::QualifiedName => scalar_or_list(value, K::QualifiedName, |item| {
            let name = item.extract::<QualifiedName>()?;
            Ok(V::QualifiedName(Box::new(name.as_ua_ref().clone())))
        }),
        D::LocalizedText => scalar_or_list(value, K::LocalizedText, |item| {
            Ok(localized_text(&item.extract::<LocalizedText>()?))
        }),
        D::Float => scalar_or_list(value, K::Float, |item| {
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

pub(crate) fn localized_text(value: &LocalizedText) -> ua::Variant {
    ua::Variant::LocalizedText(Box::new(value.as_ua_ref().clone()))
}

fn integer<T: TryFrom<i128>>(
    value: &Bound<'_, PyAny>,
    kind: ua::VariantScalarTypeId,
    variant: impl Fn(T) -> ua::Variant,
) -> PyResult<ua::Variant> {
    scalar_or_list(value, kind, |item| {
        let NativeInteger(number) = item.extract::<NativeInteger<T>>()?;
        Ok(variant(number))
    })
}

fn double(value: &Bound<'_, PyAny>) -> PyResult<f64> {
    if value.is_instance_of::<PyBool>() {
        return Err(PyTypeError::new_err("expected a real number"));
    }

    value.extract()
}

fn scalar_or_list(
    value: &Bound<'_, PyAny>,
    kind: ua::VariantScalarTypeId,
    encode: impl Fn(&Bound<'_, PyAny>) -> PyResult<ua::Variant>,
) -> PyResult<ua::Variant> {
    let Ok(list) = value.cast::<PyList>() else {
        return encode(value);
    };

    let values = list
        .iter()
        .map(|item| encode(&item))
        .collect::<PyResult<_>>()?;

    Ok(ua::Variant::Array(Box::new(ua::Array {
        value_type: kind,
        values,
        dimensions: None,
    })))
}
