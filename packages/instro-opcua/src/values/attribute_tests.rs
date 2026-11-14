use pyo3::types::PyList;

use super::*;
use crate::test_support::status;
use crate::test_support::with_python;
use crate::values::payload::BoundedValue;
use crate::values::payload::PayloadError::NotSupported;
use crate::values::payload::PayloadError::TypeMismatch;

#[test]
fn payload_errors_are_deferred_without_replacing_metadata() -> PyResult<()> {
    with_python(|py| {
        let native = ua::DataValue {
            value: Some(ua::Variant::Double(2.5)),
            status: Some(ua::StatusCode::UncertainInitialValue),
            source_timestamp: Some(ua::DateTime::from(116_444_736_000_000_001_i64)),
            source_picoseconds: Some(1_399),
            server_picoseconds: Some(999),
            ..Default::default()
        };

        let result = PyAttributeValue::from_native(ua::AttributeId::DisplayName, native);
        let result = Bound::new(py, result)?;
        let failure = result.getattr("value").unwrap_err();
        assert_eq!(status(failure)?, 0x8074_0000);

        let result = result.borrow();
        assert_eq!(result.status().as_ua_ref().bits(), 0x4092_0000);
        assert_eq!(result.source_unix_timestamp(), Some(113));
        assert_eq!(result.server_unix_timestamp(), None);
        Ok(())
    })
}

#[test]
fn absent_and_empty_payloads_preserve_effective_good() -> PyResult<()> {
    with_python(|py| {
        for value in [None, Some(ua::Variant::Empty)] {
            let native = ua::DataValue {
                value,
                ..Default::default()
            };

            let result = PyAttributeValue::from_native(ua::AttributeId::DisplayName, native);
            assert!(result.value(py)?.is_none(py));
            assert_eq!(result.status().as_ua_ref().bits(), 0);
        }

        Ok(())
    })
}

#[test]
fn fixed_integer_payloads_reject_numeric_casts() {
    assert_eq!(
        i32::decode(&ua::Variant::Double(1.0)).err(),
        Some(TypeMismatch)
    );
}

#[test]
fn array_dimensions_reject_scalar_and_empty_wrong_kind_payloads() {
    assert_eq!(
        Vec::<u32>::decode(&ua::Variant::UInt32(1)).err(),
        Some(TypeMismatch)
    );

    let string_dimensions = ua::Array {
        value_type: ua::VariantScalarTypeId::String,
        values: vec![],
        dimensions: None,
    };

    let payload = ua::Variant::Array(Box::new(string_dimensions));
    assert_eq!(Vec::<u32>::decode(&payload).err(), Some(TypeMismatch));
}

#[test]
fn bounded_values_reject_multidimensional_arrays() {
    let matrix = ua::Array {
        value_type: ua::VariantScalarTypeId::UInt32,
        values: vec![ua::Variant::UInt32(1)],
        dimensions: Some(vec![1, 1]),
    };

    let payload = ua::Variant::Array(Box::new(matrix));
    assert_eq!(BoundedValue::decode(&payload).err(), Some(NotSupported));
}

#[test]
fn structure_definitions_decode_optional_fields() -> PyResult<()> {
    with_python(|py| {
        let field = ua::StructureField {
            is_optional: true,
            ..Default::default()
        };

        let definition = ua::StructureDefinition {
            structure_type: ua::StructureType::StructureWithOptionalFields,
            fields: Some(vec![field]),
            ..Default::default()
        };

        let payload = ua::Variant::ExtensionObject(ua::ExtensionObject::from_message(definition));
        let definition = payload::Definition::decode(&payload)?.to_python(py)?;
        let fields = definition.bind(py).getattr("fields")?;
        let field = fields.get_item(0)?;
        assert!(field.getattr("is_optional")?.is_truthy()?);
        Ok(())
    })
}

#[test]
fn fixed_role_lists_reject_mixed_bodies_accepted_by_value() -> PyResult<()> {
    with_python(|py| {
        let role = ua::RolePermissionType::default();

        let role = ua::Variant::ExtensionObject(ua::ExtensionObject::from_message(role));
        let field = ua::EnumField {
            value: 7,
            ..Default::default()
        };

        let field = ua::Variant::ExtensionObject(ua::ExtensionObject::from_message(field));
        let mixed = ua::Array {
            value_type: ua::VariantScalarTypeId::ExtensionObject,
            values: vec![role, field],
            dimensions: None,
        };

        let payload = ua::Variant::Array(Box::new(mixed));
        assert_eq!(
            Vec::<RolePermissionType>::decode(&payload).err(),
            Some(TypeMismatch)
        );

        let value = BoundedValue::decode(&payload)?.to_python(py)?;
        let list = value.bind(py).cast::<PyList>()?;
        assert!(list.get_item(0)?.is_instance_of::<RolePermissionType>());
        assert_eq!(list.get_item(1)?.getattr("value")?.extract::<i64>()?, 7);
        Ok(())
    })
}

#[test]
fn node_id_ordering_uses_native_fields_and_string_lengths() -> PyResult<()> {
    with_python(|py| {
        let module = crate::test_support::module(py)?;
        py.run(
            c"
assert NodeId('ns=2;i=2') < NodeId('ns=2;i=10')
assert NodeId('ns=1;s=z') < NodeId('ns=2;i=0')
assert NodeId('i=100') < NodeId('s=a')
assert NodeId('s=z') < NodeId('s=aa')
assert NodeId('g=000000ff-0000-0000-0000-000000000000') < NodeId('g=00000100-0000-0000-0000-000000000000')
",
            Some(&module.dict()),
            None,
        )
    })
}

#[test]
fn null_and_empty_arrays_compare_equal_with_consistent_hashes() -> PyResult<()> {
    with_python(|py| {
        let null = Bound::new(py, RelativePath::new(ua::RelativePath { elements: None }))?;
        let empty = ua::RelativePath {
            elements: Some(vec![]),
        };

        let empty = Bound::new(py, RelativePath::new(empty))?;
        assert!(null.eq(&empty)?);
        assert_eq!(null.hash()?, empty.hash()?);

        Ok(())
    })
}
