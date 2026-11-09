use instro_opcua::prelude::*;
use itertools::Itertools as _;
use pyo3::IntoPyObjectExt as _;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyString;

pub(crate) trait UaType {
    type Inner: open62541::DataTypeExt;
    fn new(value: Self::Inner) -> Self;
    fn as_ua_ref(&self) -> &Self::Inner;
}

macro_rules! ua_struct {
    ($(#[doc = $doc:literal])? $struct_vis:vis struct $this_t:tt;) => {
        $(#[$doc])?
        #[pyclass(from_py_object, eq, ord)]
        #[repr(transparent)]
        #[derive(Clone, PartialEq, PartialOrd)]
        $struct_vis struct $this_t(::instro_opcua::open62541::ua::$this_t);

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

macro_rules! ua_enum {
    ($(#[$($attrs:tt)+])* $vis:vis enum $this_t:ident { $($rest:tt)+ }) => {
        // preserve arbitrary attributes while intercepting the #[optionset] attribute
        ua_enum!(@declare_type [] [no] $vis enum $this_t; $(#[$($attrs)+])*);
        ua_enum!(@impl $this_t { $($rest)+ });
    };

    (
        @declare_type [$(#[$attrs:meta])*] [$optionset:ident]
        $vis:vis enum $this_t:ident;
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
        #[pyclass(skip_from_py_object, eq)]
        #[repr(transparent)]
        #[derive(Clone, PartialEq, Eq, PartialOrd, Ord)]
        $vis struct $this_t(::instro_opcua::open62541::ua::$this_t);
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

    (@impl $this_t:ident { $($(#[$variant_doc:meta])* $variant:ident),* $(,)? }) => {
        paste::paste! {
            #[pymethods]
            impl $this_t {
                $(
                    $(#[$variant_doc])?
                    #[classattr]
                    pub const [<$variant:snake:upper>]: $this_t = Self(::instro_opcua::open62541::ua::$this_t::[<$variant:upper>]);
                )*
            }

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

/// Normalizes a number of nanoseconds to a number of seconds and nanoseconds.
fn normalize_nanos(nanos: impl Into<i128>) -> (i64, u32) {
    let nanos = nanos.into();

    let secs = nanos.div_euclid(1_000_000_000);
    let rem_nanos = nanos.rem_euclid(1_000_000_000);

    #[expect(clippy::as_conversions, reason = "heat death of the universe")]
    (secs as i64, rem_nanos as u32)
}

fn normalize_timestamp(dt: ua::DateTime) -> (i64, u32) {
    let nanos = dt.as_unix_timestamp_nanos();
    normalize_nanos(nanos)
}

// NOTE: `picos_intervals` is the number of 10-picosecond intervals, not raw picoseconds
fn normalize_value_timestamp(
    ua_dt: Option<ua::DateTime>,
    picos_intervals: Option<u16>,
) -> Option<chrono::DateTime<chrono::Utc>> {
    let (secs, rem_nanos) = normalize_timestamp(ua_dt?);
    let rem_nanos = rem_nanos + picos_intervals.unwrap_or(0).saturating_div(100) as u32;
    let (extra_secs, rem_nanos) = normalize_nanos(rem_nanos);

    chrono::DateTime::from_timestamp(secs + extra_secs, rem_nanos)
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
        .map(Some)
    }
}

ua_struct! {
    pub struct QualifiedName;
}

#[pymethods]
impl QualifiedName {
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
}

ua_struct! {
    pub struct ExpandedNodeId;
}

#[pymethods]
impl ExpandedNodeId {
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
    #[optionset]
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
        None,
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
        None,
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
        None,
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

#[pymethods]
impl LocalizedText {
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
}

ua_enum! {
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

#[pymethods]
impl AttributeWriteMask {
    #[getter]
    pub fn is_access_level_set(&self) -> bool {
        self.0.access_level()
    }

    #[getter]
    pub fn is_access_level_ex_set(&self) -> bool {
        self.0.access_level_ex()
    }

    #[getter]
    pub fn is_access_restrictions_set(&self) -> bool {
        self.0.access_restrictions()
    }

    #[getter]
    pub fn array_dimensions(&self) -> bool {
        self.0.array_dimensions()
    }

    #[getter]
    pub fn browse_name(&self) -> bool {
        self.0.browse_name()
    }

    #[getter]
    pub fn contains_no_loops(&self) -> bool {
        self.0.contains_no_loops()
    }

    #[getter]
    pub fn data_type(&self) -> bool {
        self.0.data_type()
    }

    #[getter]
    pub fn data_type_definition(&self) -> bool {
        self.0.data_type_definition()
    }

    #[getter]
    pub fn description(&self) -> bool {
        self.0.description()
    }

    #[getter]
    pub fn display_name(&self) -> bool {
        self.0.display_name()
    }

    #[getter]
    pub fn event_notifier(&self) -> bool {
        self.0.event_notifier()
    }

    #[getter]
    pub fn executable(&self) -> bool {
        self.0.executable()
    }

    #[getter]
    pub fn historizing(&self) -> bool {
        self.0.historizing()
    }

    #[getter]
    pub fn inverse_name(&self) -> bool {
        self.0.inverse_name()
    }

    #[getter]
    pub fn is_abstract(&self) -> bool {
        self.0.is_abstract()
    }

    #[getter]
    pub fn minimum_sampling_interval(&self) -> bool {
        self.0.minimum_sampling_interval()
    }

    #[getter]
    pub fn node_class(&self) -> bool {
        self.0.node_class()
    }

    #[getter]
    pub fn node_id(&self) -> bool {
        self.0.node_id()
    }

    #[getter]
    pub fn role_permissions(&self) -> bool {
        self.0.role_permissions()
    }

    #[getter]
    pub fn symmetric(&self) -> bool {
        self.0.symmetric()
    }

    #[getter]
    pub fn user_access_level(&self) -> bool {
        self.0.user_access_level()
    }

    #[getter]
    pub fn user_executable(&self) -> bool {
        self.0.user_executable()
    }

    #[getter]
    pub fn user_write_mask(&self) -> bool {
        self.0.user_write_mask()
    }

    #[getter]
    pub fn value_for_variable_type(&self) -> bool {
        self.0.value_for_variable_type()
    }

    #[getter]
    pub fn value_rank(&self) -> bool {
        self.0.value_rank()
    }

    #[getter]
    pub fn write_mask(&self) -> bool {
        self.0.write_mask()
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
        None,
        HistoryRead,
        HistoryWrite,
        SubscribeToEvents,
    }
}

#[pymethods]
impl EventNotifierType {
    pub fn is_none_set(&self) -> bool {
        self.0.none()
    }

    pub fn is_history_read_set(&self) -> bool {
        self.0.history_read()
    }

    pub fn is_history_write_set(&self) -> bool {
        self.0.history_write()
    }

    pub fn is_subscribe_to_events_set(&self) -> bool {
        self.0.subscribe_to_events()
    }
}

ua_struct! {
    pub struct SecurityLevel;
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

#[pymethods]
impl EndpointDescription {
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
    fn security_level(&self) -> SecurityLevel {
        self.0.security_level().into()
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
    fn pre_epoch_fractional_timestamp_has_nonnegative_nanoseconds() {
        let timestamp = ua::DateTime::try_from_unix_timestamp_nanos(-500_000_000).unwrap();

        let normalized = normalize_timestamp(timestamp);

        assert_eq!(normalized, (-1, 500_000_000));
    }

    #[test]
    fn data_value_preserves_distinct_source_and_server_timestamps() {
        let value = DataValue(
            ua::DataValue::new(ua::Variant::scalar(ua::Double::new(1.0)))
                .with_source_timestamp(
                    ua::DateTime::try_from_unix_timestamp_nanos(1_250_000_000).unwrap(),
                )
                .with_server_timestamp(
                    ua::DateTime::try_from_unix_timestamp_nanos(2_750_000_000).unwrap(),
                )
                .cast(),
        );

        let source = value.source_timestamp();
        let server = value.server_timestamp();

        assert_eq!(source, Some("1970-01-01T00:00:01.250Z".parse().unwrap()));
        assert_eq!(server, Some("1970-01-01T00:00:02.750Z".parse().unwrap()));
    }
}
