use instro_ethernetip_rs::{StructuredValue as RustStructuredValue, Value};
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBool, PyByteArray, PyBytes, PyFloat, PyInt};

#[pyclass(module = "instro.unstable._ethernetip", skip_from_py_object)]
#[derive(Clone, Debug)]
/// Explicit PLC value wrapper that preserves the underlying EtherNet/IP scalar kind.
///
/// Python's native `int` and `float` types do not carry enough information to distinguish PLC
/// types such as `DINT` vs `UDINT` or `REAL` vs `LREAL`. `PlcValue` keeps that tag type explicit
/// so read results can be written back losslessly.
pub(crate) struct PlcValue {
    value: Value,
}

#[pyclass(
    eq,
    module = "instro.unstable._ethernetip",
    rename_all = "UPPERCASE",
    skip_from_py_object
)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
/// PLC kind for a `PlcValue`.
///
/// This mirrors the discriminant of the Rust `Value` enum wrapped by `PlcValue`.
///
/// In Rust, the `Value` enum is effectively already tagged, e.g. `Value::Dint(1)`.
/// However, when exposed as a plain Python payload those collapse:
/// 1 could be SINT, INT, DINT, LINT, USINT, UINT, UDINT, or ULINT
/// 1.0 could be REAL or LREAL
/// The additional `PlcKind` enum provides the tag type explicitly for Python.
pub(crate) enum PlcKind {
    Bool,
    Sint,
    Int,
    Dint,
    Lint,
    Usint,
    Uint,
    Udint,
    Ulint,
    Real,
    Lreal,
    Structured,
}

impl From<Value> for PlcValue {
    fn from(value: Value) -> Self {
        Self { value }
    }
}

impl PlcValue {
    fn clone_value(&self) -> Value {
        self.value.clone()
    }
}

#[pyclass(module = "instro.unstable._ethernetip", skip_from_py_object)]
#[derive(Clone, Debug)]
/// Opaque bytes for a structured PLC value.
///
/// Structured values are returned when the Rust EtherNet/IP layer cannot decode a tag into one
/// of the built-in scalar variants. The `symbol_id` is optional because some read paths do not
/// expose it; when absent, the lower-level client can recover it during a write if the PLC
/// supports tag-attribute lookup. `StructuredValue` is the payload for structured `PlcValue`
/// instances rather than a top-level PLC value on its own.
pub(crate) struct StructuredValue {
    symbol_id: Option<i32>,
    data: Vec<u8>,
}

impl From<RustStructuredValue> for StructuredValue {
    fn from(value: RustStructuredValue) -> Self {
        Self {
            symbol_id: value.symbol_id,
            data: value.data,
        }
    }
}

impl From<&StructuredValue> for RustStructuredValue {
    /// Copy the Python wrapper into the core crate's structured payload type.
    ///
    /// The Python object owns its bytes, so the conversion clones the payload before handing it
    /// to the Rust session API.
    fn from(value: &StructuredValue) -> Self {
        RustStructuredValue {
            symbol_id: value.symbol_id,
            data: value.data.clone(),
        }
    }
}

#[pymethods]
impl PlcValue {
    #[staticmethod]
    /// Create a `BOOL` PLC value.
    fn bool(value: bool) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create a `SINT` PLC value.
    fn sint(value: i8) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create an `INT` PLC value.
    fn int(value: i16) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create a `DINT` PLC value.
    fn dint(value: i32) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create a `LINT` PLC value.
    fn lint(value: i64) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create a `USINT` PLC value.
    fn usint(value: u8) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create a `UINT` PLC value.
    fn uint(value: u16) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create a `UDINT` PLC value.
    fn udint(value: u32) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create a `ULINT` PLC value.
    fn ulint(value: u64) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create a `REAL` PLC value.
    fn real(value: f32) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create an `LREAL` PLC value.
    fn lreal(value: f64) -> Self {
        Self {
            value: Value::from(value),
        }
    }

    #[staticmethod]
    /// Create a structured PLC value from a `StructuredValue` payload.
    fn structured(value: PyRef<'_, StructuredValue>) -> Self {
        Self {
            value: Value::Struct(RustStructuredValue::from(&*value)),
        }
    }

    #[getter]
    /// PLC kind discriminant for this value, such as `PlcKind.DINT` or `PlcKind.REAL`.
    ///
    /// This mirrors the discriminant of the Rust `Value` enum wrapped by `PlcValue`.
    fn kind(&self) -> PyResult<PlcKind> {
        py_kind(&self.value)
    }

    #[getter]
    /// Python payload for this PLC value.
    ///
    /// Numeric PLC types become Python `int`/`float`, and structured values become
    /// `StructuredValue`. The `PlcValue.kind` property preserves the original PLC kind.
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        value_payload_to_py(py, &self.value)
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let value = value_payload_to_py(py, &self.value)?;
        let value_repr = value.bind(py).repr()?.extract::<String>()?;
        Ok(format!(
            "PlcValue(kind=PlcKind.{}, value={})",
            self.kind()?.name(),
            value_repr
        ))
    }
}

impl PlcKind {
    fn name(self) -> &'static str {
        match self {
            Self::Bool => "BOOL",
            Self::Sint => "SINT",
            Self::Int => "INT",
            Self::Dint => "DINT",
            Self::Lint => "LINT",
            Self::Usint => "USINT",
            Self::Uint => "UINT",
            Self::Udint => "UDINT",
            Self::Ulint => "ULINT",
            Self::Real => "REAL",
            Self::Lreal => "LREAL",
            Self::Structured => "STRUCTURED",
        }
    }
}

#[pymethods]
impl StructuredValue {
    #[new]
    #[pyo3(signature = (symbol_id=None, data=None))]
    /// Create a structured PLC payload from raw bytes.
    fn new(symbol_id: Option<i32>, data: Option<&Bound<'_, PyAny>>) -> PyResult<Self> {
        let data = match data {
            Some(data) => data.extract::<Vec<u8>>()?,
            None => Vec::new(),
        };

        Ok(Self { symbol_id, data })
    }

    #[getter]
    /// Optional template instance id used for structured PLC writes.
    fn symbol_id(&self) -> Option<i32> {
        self.symbol_id
    }

    #[getter]
    /// Raw PLC payload bytes.
    ///
    /// Note that this copies the data.
    fn data<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, &self.data)
    }

    /// Return the raw payload as Python `bytes`.
    ///
    /// Note that this copies the data.
    fn __bytes__<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, &self.data)
    }

    fn __repr__(&self) -> String {
        format!(
            "StructuredValue(symbol_id={:?}, data={:?})",
            self.symbol_id, self.data
        )
    }
}

/// Convert the Rust crate's public `Value` enum into a Python object.
///
/// Values stay wrapped in `PlcValue` so Python callers can preserve the PLC scalar kind and write
/// tags back losslessly.
pub(crate) fn rust_value_to_py(py: Python<'_>, value: Value) -> PyResult<Py<PyAny>> {
    if matches!(value, Value::String(_)) {
        return Err(PyTypeError::new_err(
            "PLC string values are not exposed through the Python EtherNet/IP API",
        ));
    }

    Py::new(py, PlcValue::from(value))?.into_py_any(py)
}

/// Convert an accepted Python write payload into the Rust crate's public `Value` enum.
///
/// The binding intentionally accepts only explicit PLC values plus a narrow set of unambiguous
/// Python types. Bare Python numerics and strings are rejected, and structured payloads are
/// promoted to structured PLC values automatically.
pub(crate) fn py_to_value(value: &Bound<'_, PyAny>) -> PyResult<Value> {
    // `PyBool` is a subclass of `PyInt` so it MUST be checked before `PyInt`
    if value.is_instance_of::<PyBool>() {
        return Ok(Value::from(value.extract::<bool>()?));
    }

    if value.is_instance_of::<PlcValue>() {
        let plc_value = value.extract::<PyRef<'_, PlcValue>>()?;
        return Ok(plc_value.clone_value());
    }

    if value.is_instance_of::<StructuredValue>() {
        let structured_value = value.extract::<PyRef<'_, StructuredValue>>()?;
        return Ok(Value::Struct(RustStructuredValue::from(&*structured_value)));
    }

    if value.is_instance_of::<PyBytes>() || value.is_instance_of::<PyByteArray>() {
        return Err(PyTypeError::new_err(
            "write_tag rejects bare bytes and bytearray; wrap them with StructuredValue(data=...)",
        ));
    }

    if value.is_instance_of::<PyInt>() {
        return Err(PyTypeError::new_err(
            "write_tag rejects bare Python ints; use PlcValue.sint/int/dint/lint/usint/uint/udint/ulint",
        ));
    }

    if value.is_instance_of::<PyFloat>() {
        return Err(PyTypeError::new_err(
            "write_tag rejects bare Python floats; use PlcValue.real or PlcValue.lreal",
        ));
    }

    Err(PyTypeError::new_err(
        "write_tag accepts PlcValue, StructuredValue, or bool",
    ))
}

fn py_kind(value: &Value) -> PyResult<PlcKind> {
    Ok(match value {
        Value::Bool(_) => PlcKind::Bool,
        Value::Sint(_) => PlcKind::Sint,
        Value::Int(_) => PlcKind::Int,
        Value::Dint(_) => PlcKind::Dint,
        Value::Lint(_) => PlcKind::Lint,
        Value::Usint(_) => PlcKind::Usint,
        Value::Uint(_) => PlcKind::Uint,
        Value::Udint(_) => PlcKind::Udint,
        Value::Ulint(_) => PlcKind::Ulint,
        Value::Real(_) => PlcKind::Real,
        Value::Lreal(_) => PlcKind::Lreal,
        Value::String(_) => {
            return Err(PyTypeError::new_err(
                "PLC string values are not exposed through the Python EtherNet/IP API",
            ));
        }
        Value::Struct(_) => PlcKind::Structured,
    })
}

fn value_payload_to_py(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
    Ok(match value {
        Value::Bool(value) => (*value).into_py_any(py)?,
        Value::Sint(value) => (*value).into_py_any(py)?,
        Value::Int(value) => (*value).into_py_any(py)?,
        Value::Dint(value) => (*value).into_py_any(py)?,
        Value::Lint(value) => (*value).into_py_any(py)?,
        Value::Usint(value) => (*value).into_py_any(py)?,
        Value::Uint(value) => (*value).into_py_any(py)?,
        Value::Udint(value) => (*value).into_py_any(py)?,
        Value::Ulint(value) => (*value).into_py_any(py)?,
        Value::Real(value) => (*value).into_py_any(py)?,
        Value::Lreal(value) => (*value).into_py_any(py)?,
        Value::String(_) => {
            return Err(PyTypeError::new_err(
                "PLC string values are not exposed through the Python EtherNet/IP API",
            ));
        }
        Value::Struct(value) => {
            Py::new(py, StructuredValue::from(value.clone()))?.into_py_any(py)?
        }
    })
}
