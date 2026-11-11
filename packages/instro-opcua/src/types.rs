use std::hash::DefaultHasher;
use std::hash::Hash;
use std::hash::Hasher;

use instro_opcua::prelude::*;
use itertools::Itertools as _;
use pyo3::IntoPyObjectExt as _;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::py_format;
use pyo3::types::PyString;

/// Encodes the relationship between an open62541 type definition and its python wrapper.
pub(crate) trait UaType {
    type Inner: open62541::DataTypeExt;

    /// Constructs [`Self`] from a [`Self::Inner`].
    fn new(value: Self::Inner) -> Self;

    /// Returns a reference to the underlying [`Self::Inner`].
    fn as_ua_ref(&self) -> &Self::Inner;
}

/// Generates a python wrapper for a UA structured type
macro_rules! ua_struct {
    ($(#[$attrs:meta])* $vis:vis struct $this_t:tt;) => {
        $(#[$attrs])*
        #[pyclass(from_py_object, eq, ord)]
        #[repr(transparent)]
        #[derive(Clone, PartialEq, PartialOrd, Debug)]
        $vis struct $this_t(::instro_opcua::open62541::ua::$this_t);

        impl std::ops::Deref for $this_t {
            type Target = ::instro_opcua::open62541::ua::$this_t;

            fn deref(&self) -> &Self::Target {
                &self.0
            }
        }

        impl From<$this_t> for ::instro_opcua::open62541::ua::$this_t {
            fn from(value: $this_t) -> ::instro_opcua::open62541::ua::$this_t {
                value.0
            }
        }

        impl From<::instro_opcua::open62541::ua::$this_t> for $this_t {
            fn from(value: ::instro_opcua::open62541::ua::$this_t) -> $this_t {
                $this_t::new(value)
            }
        }

        impl UaType for $this_t {
            type Inner = ::instro_opcua::open62541::ua::$this_t;

            fn new(value: ::instro_opcua::open62541::ua::$this_t) -> Self {
                Self(value)
            }

            fn as_ua_ref(&self) -> &::instro_opcua::open62541::ua::$this_t {
                &self.0
            }
        }
    }
}

/// Generates a python wrapper for a UA enumerated type.
/// Usable both for both simple and optionset (i.e. bitmask) enums.
macro_rules! ua_enum {
    ($(#[$($attrs:tt)+])* $vis:vis enum $this_t:ident { $($variants:tt)+ }) => {
        // preserve arbitrary attributes while intercepting the #[optionset] attribute
        ua_enum!(@declare_type [] [no] $vis enum $this_t; $(#[$($attrs)+])* { $($variants)+ });
    };

    (
        @declare_type [$(#[$attrs:meta])*] [$optionset:ident]
        $vis:vis enum $this_t:ident;
        { $($variants:tt)+ }
    ) => {
        paste::paste! {
            impl $this_t {
                ua_enum!(@when $optionset {
                    #[doc = "Gets the value of this [`" $this_t "`] as a [`u32`]."]
                    pub fn as_u32(&self) -> u32 {
                        self.0.into_raw() as u32
                    }
                } else {
                    #[doc = "Gets the value of this [`" $this_t "`] as a [`u32`]."]
                    pub fn as_u32(&self) -> u32 {
                        self.0.as_u32()
                    }
                });
            }
        }

        $(#[$attrs])*
        #[pyclass(skip_from_py_object, eq, frozen)]
        #[repr(transparent)]
        #[derive(Clone, PartialEq, Eq, PartialOrd, Ord, Debug)]
        $vis struct $this_t(::instro_opcua::open62541::ua::$this_t);

        #[pymethods]
        impl $this_t {
            pub fn __hash__(&self) -> u64 {
                let mut hasher = DefaultHasher::new();
                self.as_u32().hash(&mut hasher);
                hasher.finish()
            }
        }

        ua_enum!(@impl [$optionset] $this_t { $($variants)+ });
    };

    (
        @declare_type [$(#[$($attrs:tt)+])*] [$optionset:ident]
        $vis:vis enum $this_t:ident; #[optionset]
        $($rest:tt)*
    ) => {
        ua_enum!(
            @declare_type [$(#[$attrs])*] [yes]
            $vis enum $this_t; $($rest)*
        );
    };

    (
        @declare_type [$(#[$($attrs:tt)+])*] [$optionset:ident]
        $vis:vis enum $this_t:ident; #[attr:meta]
        $($rest:tt)*
    ) => {
        ua_enum!(
            @declare_type [$(#[$attrs])* #[$attr]] [$optionset]
            $vis enum $this_t; $($rest)*
        );
    };


    (@when no { $item:item } $(else { $else:item } )?) => { $($else)? };
    (@when yes { $item:item } $(else { $else:item } )?) => { $item };

    (@is_none_variant None) => { yes };
    (@is_none_variant $_:ident) => { no };

    (@impl [$optionset:ident] $this_t:ident { $($(#[$variant_doc:meta])* $variant:ident),* $(,)? }) => {
        paste::paste! {
            #[pymethods]
            impl $this_t {
                $(
                    $(#[$variant_doc])?
                    #[classattr]
                    pub const [<$variant:snake:upper>]: $this_t = Self(::instro_opcua::open62541::ua::$this_t::[<$variant:upper>]);
                )*
            }

            ua_enum!(@when $optionset {
                #[pymethods]
                impl $this_t {
                    #[classattr]
                    pub const NONE: $this_t = Self(::instro_opcua::open62541::ua::$this_t::NONE);

                    $(
                        pub fn [<is_ $variant:snake _set>](&self) -> bool {
                            self.0 & (Self::[<$variant:snake:upper>].0) == Self::[<$variant:snake:upper>].0
                        }
                    )*

                    pub fn __and__(&self, other: &$this_t) -> $this_t {
                        Self(self.0 & other.0)
                    }

                    pub fn __or__(&self, other: &$this_t) -> $this_t {
                        Self(self.0 | other.0)
                    }
                }
            });

            impl From<::instro_opcua::open62541::ua::$this_t> for $this_t {
                fn from(value: ::instro_opcua::open62541::ua::$this_t) -> $this_t {
                    $this_t::new(value)
                }
            }

            impl From<$this_t> for ::instro_opcua::open62541::ua::$this_t {
                fn from(value: $this_t) -> ::instro_opcua::open62541::ua::$this_t {
                    value.0
                }
            }

            impl UaType for $this_t {
                type Inner = ::instro_opcua::open62541::ua::$this_t;

                #[allow(unused, reason = "not used for all enums")]
                fn new(value: ::instro_opcua::open62541::ua::$this_t) -> Self {
                    Self(value)
                }

                #[allow(unused, reason = "not used for all enums")]
                fn as_ua_ref(&self) -> &::instro_opcua::open62541::ua::$this_t {
                    &self.0
                }
            }

            impl TryFrom<u32> for $this_t {
                type Error = PyErr;

                fn try_from(value: u32) -> PyResult<Self> {
                    use ::instro_opcua::open62541::ua;
                    Ok(match value {
                        $(ua::$this_t::[<$variant:upper _U32>] => Self(ua::$this_t::[<$variant:upper>]),)*
                        _ => Err(PyValueError::new_err(format!("invalid attribute id: '{value}'")))?,
                    })
                }
            }

            #[pymethods]
            impl $this_t {
                pub fn __str__<'py>(&self, py: Python<'py>) -> Bound<'py, PyString> {
                    use ::instro_opcua::open62541::ua;
                    match self.0.clone() {
                        $(
                            ua::$this_t::[<$variant:upper>] => ::pyo3::intern!(py, stringify!([<$variant:lower>])).clone(),
                        )*
                        _ => ::pyo3::types::PyString::new(py, &self.clone().as_u32().to_string()),
                    }
                }
            }

            impl FromPyObject<'_, '_> for $this_t {
                type Error = PyErr;

                fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
                    use $crate::util::py_match;
                    use ::instro_opcua::open62541::ua;

                    Ok(py_match!{
                        slf: $this_t = obj => slf.borrow().clone(),

                        #[extract]
                        s: &str = obj => {
                            match s.to_lowercase().as_str() {
                                $(
                                    stringify!([<$variant:lower>]) | stringify!([<$variant:snake>]) => Self(ua::$this_t::[<$variant:upper>]),
                                )*

                                _ => Err(PyValueError::new_err(format!("invalid attribute id: '{s}'")))?,
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
}

// NOTE: `picos_intervals` is the number of 10-picosecond intervals, not raw picoseconds
fn normalize_value_timestamp(ua_dt: ua::DateTime, picos_intervals: Option<u16>) -> i128 {
    let nanos = ua_dt.as_unix_timestamp_nanos();
    let extra_nanos = picos_intervals.unwrap_or(0).saturating_div(100) as i128;
    nanos + extra_nanos
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
    pub fn server_timestamp(&self) -> Option<i128> {
        Some(normalize_value_timestamp(
            self.0.server_timestamp().copied()?,
            self.0.server_picoseconds(),
        ))
    }

    #[getter]
    pub fn source_timestamp(&self) -> Option<i128> {
        Some(normalize_value_timestamp(
            self.0.source_timestamp().copied()?,
            self.0.source_picoseconds(),
        ))
    }

    // wake me up when pyo3 supports annotations on getters
    // #[pyo3(signature = () -> "bool | int | float | str | uuid.UUID | NodeId | ExpandedNodeId | StatusCode | QualifiedName | datetime.datetime | None")]
    #[getter]
    pub fn value<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyAny>>> {
        // assume that status code is good if not present
        if let Some(status) = self.0.status()
            && !status.is_good()
        {
            Err(PyValueError::new_err(format!(
                "data value contains a non-good status code: '{}'",
                status.name()
            )))?
        }

        let variant = match self.0.value() {
            Some(value) => value,
            None => return Ok(None),
        };

        let value = match variant.to_value() {
            VariantValue::Scalar(value) => value,
            _ => Err(PyValueError::new_err(
                "data value contains a non-scalar value".to_string(),
            ))?,
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
            ScalarValue::DateTime(dt) => dt.as_unix_timestamp_nanos().into_bound_py_any(py),

            _ => Err(PyValueError::new_err(
                "unknown scalar value type".to_string(),
            )),
        }
        .map(Some)
    }
}

ua_struct! {
    #[derive(Hash)]
    pub struct QualifiedName;
}

#[pymethods]
impl QualifiedName {
    pub fn __hash__(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

    #[getter]
    pub fn name(&self) -> &str {
        self.0.name().as_str().unwrap_or("")
    }

    #[getter]
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
            "BrowsePath(starting_node='{}' relative_path='{}')",
            *self.starting_node(),
            *self.relative_path()
        )
    }
}

ua_struct! {
    pub struct StatusCode;
}

impl Hash for StatusCode {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.into_raw().hash(state);
    }
}

#[pymethods]
impl StatusCode {
    pub fn __hash__(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

    #[getter]
    pub fn is_good(&self) -> bool {
        self.0.is_good()
    }

    #[getter]
    pub fn is_bad(&self) -> bool {
        self.0.is_bad()
    }

    #[getter]
    pub fn is_uncertain(&self) -> bool {
        self.0.is_uncertain()
    }

    pub fn name(&self) -> &str {
        self.0.name()
    }

    #[getter]
    pub fn code(&self) -> u32 {
        self.0.into_raw()
    }

    pub fn __str__(&self) -> String {
        self.0.to_string()
    }

    pub fn __repr__(&self) -> String {
        format!("StatusCode(code='{}', name='{}')", self.name(), self.code(),)
    }
}

ua_struct! {
    pub struct ExpandedNodeId;
}

impl Hash for ExpandedNodeId {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.node_id().hash(state);
        self.0.server_index().hash(state);
        self.0.namespace_uri().as_bytes().hash(state);
    }
}

#[pymethods]
impl ExpandedNodeId {
    pub fn __hash__(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

    #[getter]
    pub fn node_id(&self) -> NodeId {
        NodeId(self.0.node_id().clone())
    }

    #[getter]
    pub fn server_index(&self) -> u32 {
        self.0.server_index()
    }

    #[getter]
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

impl Hash for RelativePathElement {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.reference_type_id().hash(state);
        self.0.is_inverse().hash(state);
        self.0.include_subtypes().hash(state);
        self.0.target_name().hash(state);
    }
}

#[pymethods]
impl RelativePathElement {
    pub fn __hash__(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

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

impl Hash for RelativePath {
    fn hash<H: Hasher>(&self, state: &mut H) {
        match self.0.elements() {
            Some(elements) => {
                true.hash(state);
                elements.len().hash(state);

                for element in elements {
                    // SAFETY: we know that the elements are `ua::RelativePathElement`s
                    // and `RelativePathElement` is repr(transparent) for its inner type
                    unsafe { std::mem::transmute::<&_, &RelativePathElement>(element).hash(state) };
                }
            }

            None => false.hash(state),
        }
    }
}

#[pymethods]
impl RelativePath {
    pub fn __hash__(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

    #[staticmethod]
    pub fn parse(path_str: &str) -> PyResult<Self> {
        let path = path_str
            .parse()
            .map_err(|_| PyValueError::new_err(format!("invalid relative path: '{path_str}'")))?;

        Ok(Self(path))
    }

    pub fn __str__(&self) -> String {
        self.0.to_string()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RelativePath(elements={:?})",
            self.elements_py().iter().collect_vec()
        )
    }

    #[getter]
    #[pyo3(name = "elements")]
    pub fn elements_py(&self) -> Vec<RelativePathElement> {
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
    #[derive(Hash)]
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
    pub fn __hash__(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

    #[staticmethod]
    pub fn from_string(id_str: &str) -> PyResult<Self> {
        let id = id_str
            .parse()
            .map_err(|_| PyValueError::new_err(format!("invalid node id: '{id_str}'")))?;

        Ok(Self(id))
    }

    pub fn __str__(&self) -> String {
        self.0.to_string()
    }

    pub fn __repr__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyString>> {
        py_format!(
            py,
            "NodeId(namespace_index={}, identifier={})",
            self.namespace_index(),
            self.identifier(py)?,
        )
    }

    #[getter]
    pub fn namespace_index(&self) -> u16 {
        self.0.namespace_index()
    }

    #[getter]
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
    pub enum AttributeId {
        Value,
        DataType,
        BrowseName,
        DisplayName,
        Description,
        NodeId,
        NodeClass,
        WriteMask,
        UserWriteMask,
        IsAbstract,
        Symmetric,
        InverseName,
        ContainsNoLoops,
        EventNotifier,
        ValueRank,
        ArrayDimensions,
        AccessLevel,
        UserAccessLevel,
        MinimumSamplingInterval,
        Historizing,
        Executable,
        UserExecutable,
        DataTypeDefinition,
        RolePermissions,
        UserRolePermissions,
        AccessRestrictions,
        AccessLevelEx,
    }
}

ua_enum! {
    #[optionset]
    pub enum AccessLevelType {
        CurrentRead,
        CurrentWrite,
        HistoryRead,
        HistoryWrite,
        SemanticChange,
        StatusWrite,
        TimestampWrite,
    }
}

ua_enum! {
    #[optionset]
    pub enum AccessLevelExType {
        Constant,
        CurrentRead,
        CurrentWrite,
        HistoryRead,
        HistoryWrite,
        SemanticChange,
        StatusWrite,
        TimestampWrite,
        NonatomicRead,
        NonatomicWrite,
        WriteFullArrayOnly,
        NoSubDataTypes,
        NonVolatile,
    }
}

ua_enum! {
    #[optionset]
    pub enum AccessRestrictionType {
        ApplyRestrictionsToBrowse,
        EncryptionRequired,
        SessionRequired,
        SigningRequired,
    }
}

ua_enum! {
    pub enum MessageSecurityMode {
        None,
        Sign,
        SignAndEncrypt,
    }
}

ua_enum! {
    pub enum NodeClass {
        Unspecified,
        Object,
        Variable,
        Method,
        ObjectType,
        VariableType,
        ReferenceType,
        DataType,
        View,
    }
}

ua_struct! {
    pub struct LocalizedText;
}

impl Hash for LocalizedText {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.locale().as_bytes().hash(state);
        self.0.text().as_bytes().hash(state);
    }
}

#[pymethods]
impl LocalizedText {
    pub fn __hash__(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

    #[getter]
    pub fn locale(&self) -> Option<String> {
        let locale_str = self.0.locale();
        if locale_str.is_empty() {
            None
        } else {
            Some(locale_str.to_string())
        }
    }

    #[getter]
    pub fn text(&self) -> String {
        self.0.text().to_string()
    }

    pub fn __repr__(&self) -> String {
        let locale_str = if let Some(locale) = self.locale() {
            format!("'{}'", locale)
        } else {
            "None".to_string()
        };

        format!(
            "LocalizedText(locale={}, text='{}')",
            locale_str,
            self.text(),
        )
    }
}

ua_enum! {
    #[optionset]
    pub enum AttributeWriteMask {
        AccessLevel,
        AccessLevelEx,
        AccessRestrictions,
        ArrayDimensions,
        BrowseName,
        ContainsNoLoops,
        DataType,
        DataTypeDefinition,
        Description,
        DisplayName,
        EventNotifier,
        Executable,
        Historizing,
        InverseName,
        IsAbstract,
        MinimumSamplingInterval,
        NodeClass,
        NodeId,
        RolePermissions,
        Symmetric,
        UserAccessLevel,
        UserExecutable,
        UserWriteMask,
        ValueForVariableType,
        ValueRank,
        WriteMask,
    }
}

impl UaType for bool {
    type Inner = ua::Boolean;

    fn new(value: ua::Boolean) -> Self {
        value.value()
    }

    fn as_ua_ref(&self) -> &ua::Boolean {
        // SAFETY: primitive types are repr(transparent) for their inner type
        unsafe { std::mem::transmute(&self) }
    }
}

ua_struct! {
    pub struct EnumDefinition;
}

#[pymethods]
impl EnumDefinition {
    #[getter]
    pub fn fields(&self) -> Vec<EnumField> {
        self.0
            .fields()
            .unwrap_or(&[])
            .iter()
            .cloned()
            .map(EnumField)
            .collect()
    }
}

ua_struct! {
    pub struct EnumField;
}

#[pymethods]
impl EnumField {
    #[getter]
    pub fn name(&self) -> String {
        self.0.name().to_string()
    }

    #[getter]
    pub fn description(&self) -> LocalizedText {
        self.0.description().clone().into()
    }

    #[getter]
    pub fn value(&self) -> i64 {
        self.0.value()
    }
}

ua_struct! {
    pub struct StructureDefinition;
}

#[pymethods]
impl StructureDefinition {
    #[getter]
    pub fn fields(&self) -> Vec<StructureField> {
        self.0
            .fields()
            .unwrap_or(&[])
            .iter()
            .cloned()
            .map(StructureField)
            .collect()
    }

    #[getter]
    pub fn base_data_type(&self) -> NodeId {
        self.0.base_data_type().clone().into()
    }

    #[getter]
    pub fn default_encoding_id(&self) -> NodeId {
        self.0.default_encoding_id().clone().into()
    }

    #[getter]
    pub fn structure_type(&self) -> StructureType {
        self.0.structure_type().clone().into()
    }
}

ua_enum! {
    pub enum StructureType {
        Structure,
        StructureWithOptionalFields,
        Union,
        StructureWithSubtypedValues,
        UnionWithSubtypedValues,
    }
}

ua_struct! {
    pub struct StructureField;
}

#[pymethods]
impl StructureField {
    #[getter]
    pub fn name(&self) -> String {
        self.0.name().to_string()
    }

    #[getter]
    pub fn description(&self) -> LocalizedText {
        self.0.description().clone().into()
    }

    #[getter]
    pub fn array_dimensions(&self) -> Option<Vec<u32>> {
        Some(
            self.0
                .array_dimensions()?
                .iter()
                .map(|dim| dim.value())
                .collect_vec(),
        )
    }

    #[getter]
    pub fn data_type(&self) -> NodeId {
        self.0.data_type().clone().into()
    }

    #[getter]
    pub fn value_rank(&self) -> i32 {
        self.0.value_rank()
    }

    #[getter]
    pub fn is_optional(&self) -> bool {
        self.0.is_optional()
    }

    #[getter]
    pub fn max_string_length(&self) -> u32 {
        self.0.max_string_length()
    }
}

ua_enum! {
    #[optionset]
    pub enum EventNotifierType {
        HistoryRead,
        HistoryWrite,
        SubscribeToEvents,
    }
}

ua_enum! {
    pub enum ApplicationType {
        Server,
        Client,
        ClientAndServer,
        DiscoveryServer,
    }
}

ua_struct! {
    pub struct EndpointDescription;
}

impl Hash for EndpointDescription {
    fn hash<H: Hasher>(&self, state: &mut H) {
        let endpoint = &self.0;
        endpoint.endpoint_url().as_bytes().hash(state);
        endpoint.security_policy_uri().as_bytes().hash(state);
        endpoint.transport_profile_uri().as_bytes().hash(state);
        endpoint.server_certificate().as_bytes().hash(state);
        endpoint.security_mode().as_u32().hash(state);
        endpoint.security_level().hash(state);

        let server = endpoint.server();
        server.application_uri().as_bytes().hash(state);
        server.product_uri().as_bytes().hash(state);
        server.application_name().locale().as_bytes().hash(state);
        server.application_name().text().as_bytes().hash(state);
        server.application_type().as_u32().hash(state);
        server.gateway_server_uri().as_bytes().hash(state);
        server.discovery_profile_uri().as_bytes().hash(state);
        hash_ua_strings(server.discovery_urls(), state);
        hash_user_token_policies(endpoint.user_identity_tokens(), state);
    }
}

fn hash_ua_strings<H: Hasher>(values: Option<&[ua::String]>, state: &mut H) {
    match values {
        Some(values) => {
            true.hash(state);
            values.len().hash(state);
            for value in values {
                value.as_bytes().hash(state);
            }
        }
        None => false.hash(state),
    }
}

fn hash_user_token_policies<H: Hasher>(policies: Option<&[ua::UserTokenPolicy]>, state: &mut H) {
    match policies {
        Some(policies) => {
            true.hash(state);
            policies.len().hash(state);
            for policy in policies {
                policy.policy_id().as_bytes().hash(state);
                policy.token_type().as_u32().hash(state);
                policy.issued_token_type().as_bytes().hash(state);
                policy.issuer_endpoint_url().as_bytes().hash(state);
                policy.security_policy_uri().as_bytes().hash(state);
            }
        }
        None => false.hash(state),
    }
}

#[pymethods]
impl EndpointDescription {
    pub fn __hash__(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

    #[getter]
    fn endpoint_url(&self) -> &str {
        self.0.endpoint_url().as_str().unwrap_or("")
    }

    #[getter]
    fn security_mode(&self) -> MessageSecurityMode {
        self.0.security_mode().clone().into()
    }

    #[getter]
    fn security_policy(&self) -> &str {
        self.0.security_policy_uri().as_str().unwrap_or("")
    }

    #[getter]
    fn security_level(&self) -> u8 {
        self.0.security_level().into_raw()
    }

    #[getter]
    fn server_certificate(&self) -> &[u8] {
        self.0.server_certificate().as_bytes().unwrap_or(&[])
    }

    #[getter]
    fn application_uri(&self) -> &str {
        self.0.server().application_uri().as_str().unwrap_or("")
    }

    #[getter]
    fn product_uri(&self) -> &str {
        self.0.server().product_uri().as_str().unwrap_or("")
    }

    #[getter]
    fn gateway_server_uri(&self) -> &str {
        self.0.server().gateway_server_uri().as_str().unwrap_or("")
    }

    #[getter]
    fn discovery_profile_uri(&self) -> &str {
        self.0
            .server()
            .discovery_profile_uri()
            .as_str()
            .unwrap_or("")
    }

    #[getter]
    fn discovery_urls(&self) -> Vec<&str> {
        self.0
            .server()
            .discovery_urls()
            .unwrap_or(&[])
            .iter()
            .map(|s| s.as_str().unwrap_or(""))
            .collect_vec()
    }

    #[getter]
    fn application_name(&self) -> &str {
        self.0
            .server()
            .application_name()
            .text()
            .as_str()
            .unwrap_or("")
    }

    #[getter]
    fn application_type(&self) -> ApplicationType {
        self.0.server().application_type().clone().into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn data_value_preserves_distinct_source_and_server_timestamps() {
        let value = DataValue(
            ua::DataValue::new(ua::Variant::scalar(ua::Double::new(1.0)))
                .with_source_timestamp(
                    ua::DateTime::try_from_unix_timestamp_nanos(1_250_000_000).unwrap(),
                )
                .with_source_picoseconds(100)
                .with_server_timestamp(
                    ua::DateTime::try_from_unix_timestamp_nanos(2_750_000_000).unwrap(),
                )
                .with_server_picoseconds(200)
                .cast(),
        );

        let source = value.source_timestamp();
        let server = value.server_timestamp();

        assert_eq!(source, Some(1_250_000_001));
        assert_eq!(server, Some(2_750_000_002));
    }
}
