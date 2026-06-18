//! Public value types for this crate's user-facing API.
//!
//! [`Value`] intentionally mirrors [`rust_ethernet_ip::PlcValue`], but remains a crate-owned
//! abstraction so the public API does not expose the backend's types directly. That gives us
//! room to change or replace the underlying EtherNet/IP implementation later without forcing
//! a breaking change onto callers.
//!
//! This module is responsible for converting between the public value types and the backend
//! types.

use rust_ethernet_ip::{PlcValue, UdtData};

/// User-facing wrapper for [`rust_ethernet_ip::PlcValue`] returned by this crate.
///
/// This API hides the wire-level details exposed by the underlying EtherNet/IP client.
/// Primitive PLC values are mapped into Rust primitives, backend-decoded strings are exposed
/// as plain [`String`]s, and user-defined payloads are preserved as opaque
/// [`StructuredValue`] bytes.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Bool(bool),
    Sint(i8),
    Int(i16),
    Dint(i32),
    Lint(i64),
    Usint(u8),
    Uint(u16),
    Udint(u32),
    Ulint(u64),
    Real(f32),
    Lreal(f64),
    String(String),
    Struct(StructuredValue),
}

/// User-facing wrapper for [`rust_ethernet_ip::UdtData`].
///
/// This lets callers work with structured payloads without exposing the transport
/// library's raw [`rust_ethernet_ip::UdtData`] type in the user-facing API.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StructuredValue {
    /// Symbol type identifier used by [`rust_ethernet_ip::UdtData`].
    ///
    /// This is only needed when writing a UDT back to a PLC. Reads convert a backend
    /// `symbol_id` of `0` into `None`.
    pub symbol_id: Option<i32>,
    /// Raw UDT payload bytes from [`rust_ethernet_ip::UdtData::data`].
    pub data: Vec<u8>,
}

/// Implements [`From`] conversions into [`Value`] for primitive Rust types and crate-owned
/// wrappers.
///
/// The public [`Value`] enum intentionally mirrors the PLC scalar types this crate exposes.
/// Most conversions are simple variant wrappers, so this macro keeps those impls consistent
/// and avoids repeating the same boilerplate for every supported Rust input type.
macro_rules! impl_value_from {
    ($($ty:ty => $variant:ident),* $(,)?) => {
        $(
            impl From<$ty> for Value {
                fn from(value: $ty) -> Self {
                    Self::$variant(value)
                }
            }
        )*
    };
}

impl_value_from!(
    bool => Bool,
    i8 => Sint,
    i16 => Int,
    i32 => Dint,
    i64 => Lint,
    u8 => Usint,
    u16 => Uint,
    u32 => Udint,
    u64 => Ulint,
    f32 => Real,
    f64 => Lreal,
    String => String,
    StructuredValue => Struct,
);

impl From<&str> for Value {
    fn from(value: &str) -> Self {
        Self::String(value.to_owned())
    }
}

impl From<PlcValue> for Value {
    fn from(value: PlcValue) -> Self {
        match value {
            PlcValue::Bool(value) => Value::Bool(value),
            PlcValue::Sint(value) => Value::Sint(value),
            PlcValue::Int(value) => Value::Int(value),
            PlcValue::Dint(value) => Value::Dint(value),
            PlcValue::Lint(value) => Value::Lint(value),
            PlcValue::Usint(value) => Value::Usint(value),
            PlcValue::Uint(value) => Value::Uint(value),
            PlcValue::Udint(value) => Value::Udint(value),
            PlcValue::Ulint(value) => Value::Ulint(value),
            PlcValue::Real(value) => Value::Real(value),
            PlcValue::Lreal(value) => Value::Lreal(value),
            PlcValue::String(value) => Value::String(value),
            PlcValue::Udt(udt) => udt.into(),
        }
    }
}

impl From<UdtData> for Value {
    fn from(udt: UdtData) -> Self {
        Value::Struct(StructuredValue {
            symbol_id: (udt.symbol_id != 0).then_some(udt.symbol_id),
            data: udt.data,
        })
    }
}

impl From<StructuredValue> for UdtData {
    fn from(value: StructuredValue) -> Self {
        Self {
            symbol_id: value.symbol_id.unwrap_or_default(),
            data: value.data,
        }
    }
}

impl From<StructuredValue> for PlcValue {
    fn from(value: StructuredValue) -> Self {
        PlcValue::Udt(value.into())
    }
}

impl From<Value> for PlcValue {
    fn from(value: Value) -> Self {
        match value {
            Value::Bool(value) => PlcValue::Bool(value),
            Value::Sint(value) => PlcValue::Sint(value),
            Value::Int(value) => PlcValue::Int(value),
            Value::Dint(value) => PlcValue::Dint(value),
            Value::Lint(value) => PlcValue::Lint(value),
            Value::Usint(value) => PlcValue::Usint(value),
            Value::Uint(value) => PlcValue::Uint(value),
            Value::Udint(value) => PlcValue::Udint(value),
            Value::Ulint(value) => PlcValue::Ulint(value),
            Value::Real(value) => PlcValue::Real(value),
            Value::Lreal(value) => PlcValue::Lreal(value),
            Value::String(value) => PlcValue::String(value),
            Value::Struct(value) => value.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Verifies that scalar backend PLC values map to the crate's public value variants.
    #[test]
    fn converts_scalar_plc_values() {
        let cases = vec![
            (PlcValue::Bool(true), Value::Bool(true)),
            (PlcValue::Sint(-3), Value::Sint(-3)),
            (PlcValue::Int(-12), Value::Int(-12)),
            (PlcValue::Dint(1234), Value::Dint(1234)),
            (PlcValue::Lint(-5678), Value::Lint(-5678)),
            (PlcValue::Usint(7), Value::Usint(7)),
            (PlcValue::Uint(42), Value::Uint(42)),
            (PlcValue::Udint(99), Value::Udint(99)),
            (PlcValue::Ulint(123_456), Value::Ulint(123_456)),
            (PlcValue::Real(1.25), Value::Real(1.25)),
            (PlcValue::Lreal(-9.5), Value::Lreal(-9.5)),
            (
                PlcValue::String("hello".to_owned()),
                Value::String("hello".to_owned()),
            ),
        ];

        for (input, expected) in cases {
            assert_eq!(Value::from(input), expected);
        }
    }

    /// Verifies that backend UDT bytes are exposed as a structured value instead of being decoded implicitly.
    #[test]
    fn preserves_non_string_udt_as_structured_value() {
        let udt = UdtData {
            symbol_id: 99,
            data: vec![0xde, 0xad, 0xbe, 0xef],
        };

        assert_eq!(
            Value::from(udt),
            Value::Struct(StructuredValue {
                symbol_id: Some(99),
                data: vec![0xde, 0xad, 0xbe, 0xef],
            })
        );
    }

    /// Verifies that structured values are converted back into backend UDT payloads for writes.
    #[test]
    fn converts_structured_value_back_to_plc_udt() {
        let plc_value = PlcValue::from(StructuredValue {
            symbol_id: Some(42),
            data: vec![0xaa, 0xbb],
        });

        assert_eq!(
            plc_value,
            PlcValue::Udt(UdtData {
                symbol_id: 42,
                data: vec![0xaa, 0xbb],
            })
        );
    }

    /// Verifies that public scalar values convert back to the expected backend PLC value variants.
    #[test]
    fn converts_scalar_value_back_to_plc_value() {
        assert_eq!(PlcValue::from(Value::Bool(true)), PlcValue::Bool(true));
        assert_eq!(PlcValue::from(Value::Dint(123)), PlcValue::Dint(123));
        assert_eq!(
            PlcValue::from(Value::String("hello".to_owned())),
            PlcValue::String("hello".to_owned())
        );
    }

    /// Verifies that common Rust scalar types choose the intended public value variants.
    #[test]
    fn converts_rust_scalars_into_value_variants() {
        assert_eq!(Value::from(true), Value::Bool(true));
        assert_eq!(Value::from(7_i16), Value::Int(7));
        assert_eq!(Value::from(3.5_f64), Value::Lreal(3.5));
        assert_eq!(Value::from("abc"), Value::String("abc".to_owned()));
    }

    /// Verifies that backend UDT symbol id zero is treated as the absence of a symbol id.
    #[test]
    fn preserves_zero_symbol_id_as_none_for_structured_value() {
        let udt = UdtData {
            symbol_id: 0,
            data: vec![1, 2, 3, 4],
        };

        assert_eq!(
            Value::from(udt),
            Value::Struct(StructuredValue {
                symbol_id: None,
                data: vec![1, 2, 3, 4],
            })
        );
    }
}
