use instro_opcua::prelude::*;
use pyo3::IntoPyObjectExt as _;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyString;

macro_rules! ua_struct {
    ($(#[$struct_doc:meta])? $struct_vis:vis struct $this_t:ident;) => {
        $(#[$struct_doc])?
        #[pyclass(from_py_object)]
        #[repr(transparent)]
        #[derive(Clone)]
        $struct_vis struct $this_t(::instro_opcua::open62541::ua::$this_t);

        impl std::ops::Deref for $this_t {
            type Target = ::instro_opcua::open62541::ua::$this_t;

            fn deref(&self) -> &Self::Target {
                &self.0
            }
        }

        impl $this_t {
            #[allow(unused, reason = "not used for all structs")]
            pub(crate) const fn new(value: ::instro_opcua::open62541::ua::$this_t) -> Self {
                Self(value)
            }

            #[allow(unused, reason = "not used for all structs")]
            pub(crate) fn as_ua_ref(&self) -> &::instro_opcua::open62541::ua::$this_t {
                &self.0
            }
        }
    }
}

macro_rules! ua_enum {
    ($(#[$doc:meta])? $enum_vis:vis enum $this_t:ident { $($(#[$variant_doc:meta])? $variant:ident),* $(,)? }) => {
        $(#[$doc])?
        #[pyclass(skip_from_py_object)]
        #[repr(transparent)]
        #[derive(Clone)]
        pub struct $this_t(::instro_opcua::open62541::ua::$this_t);

        #[pymethods]
        impl $this_t {
            $(
                $(#[$variant_doc])?
                #[classattr]
                pub const $variant: $this_t = Self(::instro_opcua::open62541::ua::$this_t::$variant);
            )*
        }

        impl $this_t {
            #[allow(unused, reason = "not used for all enums")]
            pub(crate) fn new(value: ua::$this_t) -> Self {
                Self(value)
            }

            #[allow(unused, reason = "not used for all enums")]
            pub(crate) fn as_ua(&self) -> ua::$this_t {
                self.0.clone()
            }
        }

        impl TryFrom<u32> for $this_t {
            type Error = PyErr;

            fn try_from(value: u32) -> PyResult<Self> {
                paste::paste! {
                    Ok(match value {
                        $(ua::$this_t::[<$variant _U32>] => Self(ua::$this_t::$variant),)*
                        _ => Err(PyValueError::new_err(format!("invalid attribute id: '{value}'")))?,
                    })
                }
            }
        }

        impl FromPyObject<'_, '_> for $this_t {
            type Error = PyErr;

            fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
                use $crate::util::py_match;

                Ok(py_match!{
                    slf: $this_t = obj => slf.borrow().clone(),

                    #[extract]
                    s: &str = obj => {
                        paste::paste! {
                            match s.to_lowercase().as_str() {
                                $(
                                    stringify!([<$variant:lower>]) => Self(ua::$this_t::$variant),
                                )*

                                _ => Err(PyValueError::new_err(format!("invalid attribute id: '{s}'")))?,
                            }
                        }
                    },

                    #[extract]
                    i: u32 = obj => Self::try_from(i)?,

                    else => {
                        Err(PyValueError::new_err(format!("invalid attribute id: '{obj:?}'")))?
                    }
                })
            }
        }
    }
}

fn normalize_timestamp(dt: ua::DateTime) -> (i64, u32) {
    let nanos = dt.as_unix_timestamp_nanos();
    let secs = nanos.div_euclid(1_000_000_000);
    let rem_nanos = nanos.rem_euclid(1_000_000_000);
    #[expect(clippy::as_conversions, reason = "heat death of the universe")]
    (secs as i64, rem_nanos as u32)
}

fn normalize_value_timestamp(
    ua_dt: Option<ua::DateTime>,
    picos: Option<u16>,
) -> Option<chrono::DateTime<chrono::Utc>> {
    let (secs, rem_nanos) = normalize_timestamp(ua_dt?);
    let rem_nanos = rem_nanos + (picos.unwrap_or(0) as u32 / 1000);
    chrono::DateTime::from_timestamp(secs, rem_nanos)
}

#[pyclass]
#[repr(transparent)]
pub struct DataValue(VariantDataValue);

impl DataValue {
    pub(crate) fn new(value: VariantDataValue) -> Self {
        Self(value)
    }
}

#[pymethods]
impl DataValue {
    #[getter]
    pub fn server_timestamp(&self) -> Option<chrono::DateTime<chrono::Utc>> {
        normalize_value_timestamp(
            self.0.server_timestamp().copied(),
            self.0.server_picoseconds(),
        )
    }

    #[getter]
    pub fn source_timestamp(&self) -> Option<chrono::DateTime<chrono::Utc>> {
        normalize_value_timestamp(
            self.0.source_timestamp().copied(),
            self.0.source_picoseconds(),
        )
    }

    #[pyo3(signature = () -> "bool | int | float | str | uuid.UUID | NodeId | ExpandedNodeId | StatusCode | QualifiedName | datetime.datetime")]
    pub fn value<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let Some(VariantValue::Scalar(value)) = self.0.value().map(ua::Variant::to_value) else {
            return Err(PyValueError::new_err(
                "data value is not a scalar value".to_string(),
            ));
        };

        match value {
            ScalarValue::Boolean(b) => b.value().into_bound_py_any(py),
            ScalarValue::SByte(i) => i.value().into_bound_py_any(py),
            ScalarValue::Byte(u) => u.value().into_bound_py_any(py),
            ScalarValue::Int16(i) => i.value().into_bound_py_any(py),
            ScalarValue::UInt16(u) => u.value().into_bound_py_any(py),
            ScalarValue::Int32(i) => i.value().into_bound_py_any(py),
            ScalarValue::UInt32(u) => u.value().into_bound_py_any(py),
            ScalarValue::Int64(i) => i.value().into_bound_py_any(py),
            ScalarValue::UInt64(u) => u.value().into_bound_py_any(py),
            ScalarValue::Float(f) => f.value().into_bound_py_any(py),
            ScalarValue::Double(d) => d.value().into_bound_py_any(py),
            ScalarValue::String(s) => s.to_string().into_bound_py_any(py),
            ScalarValue::Guid(g) => g.to_uuid().into_bound_py_any(py),
            ScalarValue::NodeId(nid) => NodeId::new(nid).into_bound_py_any(py),
            ScalarValue::ExpandedNodeId(enid) => ExpandedNodeId::new(enid).into_bound_py_any(py),
            ScalarValue::StatusCode(sc) => StatusCode::new(sc).into_bound_py_any(py),
            ScalarValue::QualifiedName(qn) => QualifiedName::new(qn).into_bound_py_any(py),
            ScalarValue::DateTime(dt) => {
                let (secs, rem_nanos) = normalize_timestamp(dt);
                let dt = chrono::DateTime::from_timestamp(secs, rem_nanos).ok_or(
                    PyValueError::new_err("failed to convert timestamp to datetime".to_string()),
                )?;

                dt.into_bound_py_any(py)
            }

            _ => Err(PyValueError::new_err(
                "unknown scalar value type".to_string(),
            )),
        }
    }
}

ua_struct! {
    pub struct QualifiedName;
}

#[pymethods]
impl QualifiedName {
    pub fn name(&self) -> &str {
        self.0.name().as_str().unwrap_or("")
    }

    pub fn namespace_index(&self) -> u16 {
        self.0.namespace_index()
    }

    pub fn __str__(&self) -> String {
        self.0.to_string()
    }
}

ua_struct! {
    pub struct BrowsePath;
}

#[pymethods]
impl BrowsePath {
    #[getter]
    pub fn starting_node(&self) -> NodeId {
        NodeId(self.0.starting_node().clone())
    }

    #[getter]
    pub fn relative_path(&self) -> RelativePath {
        RelativePath(self.0.relative_path().clone())
    }

    pub fn __repr__(&self) -> String {
        format!(
            "<BrowsePath starting_node='{}' relative_path='{}'>",
            *self.starting_node(),
            *self.relative_path()
        )
    }
}

ua_struct! {
    pub struct StatusCode;
}

#[pymethods]
impl StatusCode {
    pub fn is_good(&self) -> bool {
        self.0.is_good()
    }

    pub fn is_bad(&self) -> bool {
        self.0.is_bad()
    }

    pub fn is_uncertain(&self) -> bool {
        self.0.is_uncertain()
    }

    pub fn name(&self) -> &str {
        self.0.name()
    }

    pub fn code(&self) -> u32 {
        self.0.into_raw()
    }

    pub fn __str__(&self) -> String {
        self.0.to_string()
    }
}

ua_struct! {
    pub struct ExpandedNodeId;
}

#[pymethods]
impl ExpandedNodeId {
    pub fn node_id(&self) -> NodeId {
        NodeId(self.0.node_id().clone())
    }

    pub fn server_index(&self) -> u32 {
        self.0.server_index()
    }

    pub fn namespace_uri(&self) -> Option<String> {
        let uri_str = self.0.namespace_uri().to_string();

        if uri_str.is_empty() {
            None
        } else {
            Some(uri_str)
        }
    }

    pub fn __str__(&self) -> String {
        self.0.to_string()
    }
}

ua_struct! {
    pub struct RelativePathElement;
}

#[pymethods]
impl RelativePathElement {
    #[getter]
    pub fn target_name(&self) -> QualifiedName {
        QualifiedName(self.0.target_name().clone())
    }

    #[getter]
    pub fn reference_type_id(&self) -> NodeId {
        NodeId(self.0.reference_type_id().clone())
    }

    #[getter]
    pub fn is_inverse(&self) -> bool {
        self.0.is_inverse()
    }

    #[getter]
    pub fn include_subtypes(&self) -> bool {
        self.0.include_subtypes()
    }
}

ua_struct! {
    pub struct RelativePath;
}

#[pymethods]
impl RelativePath {
    #[staticmethod]
    pub fn parse(path_str: &str) -> PyResult<Self> {
        let path = path_str
            .parse()
            .map_err(|_| PyValueError::new_err(format!("invalid relative path: '{path_str}'")))?;

        Ok(Self(path))
    }

    pub fn __str__<'py>(&self, _py: Python<'py>) -> PyResult<Bound<'py, PyString>> {
        Ok(PyString::new(_py, &self.0.to_string()))
    }

    #[getter]
    pub fn elements(&self) -> Vec<RelativePathElement> {
        self.0
            .elements()
            .unwrap_or(&[])
            .iter()
            .cloned()
            .map(RelativePathElement)
            .collect()
    }
}

ua_struct! {
    pub struct NodeId;
}

impl NodeId {
    pub fn objects() -> Self {
        Self(ua::NodeId::ns0(open62541_sys::UA_NS0ID_OBJECTSFOLDER))
    }

    pub fn views() -> Self {
        Self(ua::NodeId::ns0(open62541_sys::UA_NS0ID_VIEWSFOLDER))
    }

    pub fn types() -> Self {
        Self(ua::NodeId::ns0(open62541_sys::UA_NS0ID_TYPESFOLDER))
    }

    pub fn root() -> Self {
        Self(ua::NodeId::ns0(open62541_sys::UA_NS0ID_ROOTFOLDER))
    }
}

#[pymethods]
impl NodeId {
    #[staticmethod]
    pub fn from_string(id_str: &str) -> PyResult<Self> {
        let id = id_str
            .parse()
            .map_err(|_| PyValueError::new_err(format!("invalid node id: '{id_str}'")))?;

        Ok(Self(id))
    }

    pub fn __str__<'py>(&self, _py: Python<'py>) -> PyResult<Bound<'py, PyString>> {
        Ok(PyString::new(_py, &self.0.to_string()))
    }

    pub fn namespace_index(&self) -> u16 {
        self.0.namespace_index()
    }

    #[pyo3(signature = () -> "int | str | bytes | uuid.UUID | None")]
    pub fn identifier<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        if let Some((_, str_id)) = self.0.as_string() {
            str_id.as_str().unwrap_or("").into_bound_py_any(py)
        } else if let Some((_, guid)) = self.0.as_guid() {
            guid.to_uuid().into_bound_py_any(py)
        } else if let Some((_, number)) = self.0.as_numeric() {
            number.into_bound_py_any(py)
        } else if let Some((_, bytestr)) = self.0.as_byte_string() {
            bytestr.as_bytes().into_bound_py_any(py)
        } else {
            Err(PyValueError::new_err(
                "node id is not identified".to_string(),
            ))
        }
    }
}

ua_enum! {
    /// Attributes that are available to read from a [`Node`]
    pub enum AttributeId {
        VALUE,
        DATATYPE,
        BROWSENAME,
        DISPLAYNAME,
        DESCRIPTION,
        NODEID,
        NODECLASS,
        WRITEMASK,
        USERWRITEMASK,
        ISABSTRACT,
        SYMMETRIC,
        INVERSENAME,
        CONTAINSNOLOOPS,
        EVENTNOTIFIER,
        VALUERANK,
        ARRAYDIMENSIONS,
        ACCESSLEVEL,
        USERACCESSLEVEL,
        MINIMUMSAMPLINGINTERVAL,
        HISTORIZING,
        EXECUTABLE,
        USEREXECUTABLE,
        DATATYPEDEFINITION,
        ROLEPERMISSIONS,
        USERROLEPERMISSIONS,
        ACCESSRESTRICTIONS,
        ACCESSLEVELEX,
    }
}

ua_enum! {
    /// Message security mode enum
    pub enum MessageSecurityMode {
        /// No signing or encryption
        NONE,
        /// Message signing only, no encryption
        SIGN,
        /// Message signing and encryption
        SIGNANDENCRYPT,
    }
}
