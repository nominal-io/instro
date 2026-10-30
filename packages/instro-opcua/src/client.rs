mod shared_rt;

use std::num::NonZeroU32;
use std::str::FromStr;
use std::time::Duration;

use chrono::Utc;
use instro_opcua::open62541::Certificate;
use instro_opcua::open62541::DataValue;
use instro_opcua::open62541::PrivateKey;
use instro_opcua::open62541::ScalarValue;
use instro_opcua::open62541::VariantValue;
use instro_opcua::prelude::*;
use itertools::Either;
use itertools::Itertools;
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyRuntimeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyString;
use pyo3::types::PyTuple;
use tokio::runtime::Runtime;

use self::shared_rt::get_runtime;
use crate::require_both;

#[pyclass]
pub struct Session(&'static Runtime, Option<StreamSession>);

#[pymethods]
impl Session {
    pub fn __enter__<'py>(this: PyRef<'py, Self>, _py: Python<'py>) -> PyRef<'py, Self> {
        this
    }

    pub fn __exit__(
        &mut self,
        py: Python<'_>,
        _exc_type: Option<Bound<'_, PyAny>>,
        _exc_value: Option<Bound<'_, PyAny>>,
        _traceback: Option<Bound<'_, PyAny>>,
    ) -> PyResult<bool> {
        py.detach(|| {
            if let Some(session) = self.1.take() {
                self.0.block_on(session.stop());
            }

            Ok(false)
        })
    }
}

impl Session {
    fn new(client: &OpcUaClient, session: StreamSession) -> Self {
        Self(client.rt, Some(session))
    }
}

#[pyclass]
#[repr(transparent)]
pub struct QualifiedName(ua::QualifiedName);

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

#[pyclass]
#[repr(transparent)]
pub struct StatusCode(ua::StatusCode);

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

    pub fn __str__<'py>(&self, _py: Python<'py>) -> PyResult<Bound<'py, PyString>> {
        Ok(PyString::new(_py, &self.0.to_string()))
    }
}

#[pyclass]
#[repr(transparent)]
pub struct ExpandedNodeId(ua::ExpandedNodeId);

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

#[pyclass]
#[repr(transparent)]
pub struct RelativePathElement(ua::RelativePathElement);

#[pymethods]
impl RelativePathElement {
    pub fn name(&self) -> QualifiedName {
        QualifiedName(self.0.target_name().clone())
    }

    pub fn reference_type_id(&self) -> NodeId {
        NodeId(self.0.reference_type_id().clone())
    }

    pub fn is_inverse(&self) -> bool {
        self.0.is_inverse()
    }

    pub fn include_subtypes(&self) -> bool {
        self.0.include_subtypes()
    }
}

#[pyclass(from_py_object)]
#[repr(transparent)]
#[derive(Clone)]
pub struct RelativePath(ua::RelativePath);

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
}

#[pyclass(from_py_object)]
#[repr(transparent)]
#[derive(Clone)]
pub struct NodeId(ua::NodeId);

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

    #[pyo3(signature = () -> "typing.Union[str, bytes, uuid.UUID] | None")]
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

fn interpret_timestamp(dt: ua::DateTime) -> (i64, u32) {
    let nanos = dt.as_unix_timestamp_nanos();
    let secs = nanos / 1_000_000_000;
    let rem_nanos = nanos % 1_000_000_000;
    #[expect(clippy::as_conversions, reason = "heat death of the universe")]
    (secs as i64, rem_nanos as u32)
}

fn normalize_value_timestamp(
    ua_dt: Option<ua::DateTime>,
    picos: Option<u16>,
) -> Option<chrono::DateTime<Utc>> {
    let (secs, rem_nanos) = interpret_timestamp(ua_dt?);
    let rem_nanos = rem_nanos + (picos.unwrap_or(0) as u32 / 1000);
    chrono::DateTime::from_timestamp(secs, rem_nanos)
}

#[pyclass]
#[repr(transparent)]
pub struct DataPoint(DataValue<ua::Variant>);

#[pymethods]
impl DataPoint {
    pub fn server_timestamp(&self) -> Option<chrono::DateTime<Utc>> {
        normalize_value_timestamp(
            self.0.server_timestamp().copied(),
            self.0.server_picoseconds(),
        )
    }

    pub fn source_timestamp(&self) -> Option<chrono::DateTime<Utc>> {
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
            ScalarValue::NodeId(nid) => NodeId(nid).into_bound_py_any(py),
            ScalarValue::ExpandedNodeId(enid) => ExpandedNodeId(enid).into_bound_py_any(py),
            ScalarValue::StatusCode(sc) => StatusCode(sc).into_bound_py_any(py),
            ScalarValue::QualifiedName(qn) => QualifiedName(qn).into_bound_py_any(py),
            ScalarValue::DateTime(dt) => {
                let (secs, rem_nanos) = interpret_timestamp(dt);
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

#[pyclass]
pub struct OpcUaClient {
    client: Client,
    rt: &'static Runtime,
}

enum NodeIdKind {
    NodeId(ua::NodeId),
    Path(ua::BrowsePath),
}

impl FromPyObject<'_, '_> for NodeIdKind {
    type Error = PyErr;

    fn extract(obj: Borrowed<'_, '_, PyAny>) -> Result<Self, Self::Error> {
        Ok(if let Ok(node_id) = obj.cast_exact::<NodeId>() {
            NodeIdKind::NodeId(node_id.borrow().0.clone())
        } else if let Ok(path) = obj.cast_exact::<RelativePath>() {
            let path = ua::BrowsePath::init()
                .with_relative_path(&path.borrow().0)
                .with_starting_node(&ua::NodeId::numeric(0, 84));

            NodeIdKind::Path(path)
        } else if let Ok(s) = obj.extract::<&str>() {
            Self::from_str(s)?
        } else {
            Err(PyValueError::new_err(format!(
                "invalid node id or path: '{}'",
                obj.as_any()
            )))?
        })
    }
}

impl FromStr for NodeIdKind {
    type Err = PyErr;
    fn from_str(id_or_path: &str) -> PyResult<Self> {
        Ok(if let Ok(node_id) = id_or_path.parse::<ua::NodeId>() {
            NodeIdKind::NodeId(node_id)
        } else if let Ok(path) = id_or_path.parse::<ua::RelativePath>() {
            let path = ua::BrowsePath::init()
                .with_relative_path(&path)
                .with_starting_node(&ua::NodeId::numeric(0, 84));

            NodeIdKind::Path(path)
        } else {
            Err(PyValueError::new_err(format!(
                "invalid node id or path: '{id_or_path}'"
            )))?
        })
    }
}

impl OpcUaClient {
    async fn translate_browse_paths(
        &self,
        paths: Vec<ua::BrowsePath>,
    ) -> PyResult<Vec<(ua::BrowsePath, ua::NodeId)>> {
        let request = ua::TranslateBrowsePathsToNodeIdsRequest::init().with_browse_paths(&paths);

        let results = self
            .client
            .with_client(async move |client| client.service_request(request).await)
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Client connection was closed: '{e}'")))?
            .map_err(|e| {
                PyRuntimeError::new_err(format!(
                    "failed to translate browse paths to node ids: '{e}'"
                ))
            })?
            .take_results()
            .map(ua::Array::into_vec)
            .unwrap_or(Vec::new());

        if results.len() != paths.len() {
            Err(PyValueError::new_err(format!(
                "failed to translate browse paths to node ids: received {} results, expected {}",
                results.len(),
                paths.len()
            )))?
        }

        let (good, bad): (Vec<_>, Vec<_>) =
            results.iter().zip(paths).partition_map(|(result, path)| {
                // this might look fragile, but it's not a big deal
                let err_str = if result.status_code().is_bad() {
                    &format!(
                        "server failed to translate browse path to node id: '{}'",
                        result.status_code().name()
                    )
                } else if let Some(targets) = result.targets() {
                    if targets.is_empty() {
                        "no targets found for browse path"
                    } else if targets.len() > 1 {
                        "ambiguous path, multiple targets found"
                    } else if targets[0].target_id().server_index() != 0 {
                        &format!(
                            "requested node exists on a different server: '{}'",
                            targets[0].target_id().server_index()
                        )
                    } else {
                        return Either::Left((path, targets[0].target_id().node_id().clone()));
                    }
                } else {
                    "internal error: browse path result array was invalid"
                };

                Either::Right(format!("'{}', {}", path.relative_path(), err_str))
            });

        if bad.is_empty() {
            Ok(good)
        } else {
            Err(PyValueError::new_err(
                "failed to intepret browse path(s):\n\t".to_string() + &bad.join("\n\t"),
            ))
        }
    }

    // TODO(cartercanedy): translate both node ids and paths and return a node ID-Path map
    fn parse_nodes(&self, nodes: Vec<NodeIdKind>) -> PyResult<Vec<ua::NodeId>> {
        let (ids, paths): (Vec<_>, Vec<_>) = nodes.into_iter().partition_map(|kind| match kind {
            NodeIdKind::NodeId(node_id) => Either::Left(node_id),
            NodeIdKind::Path(path) => Either::Right(path),
        });

        Ok(self
            .rt
            .block_on(self.translate_browse_paths(paths))?
            .into_iter()
            .map(|(_path, node_id)| node_id)
            .chain(ids)
            .collect::<Vec<_>>())
    }
}

#[pymethods]
impl OpcUaClient {
    #[staticmethod]
    #[pyo3(signature = (
        endpoint_url,
        *,
        username=None,
        password=None,
        cert=None,
        private_key=None,
        ignore_server_cert=false,
        use_generated_cert=false,
        security_mode: "typing.Literal['none', 'sign', 'sign_and_encrypt'] | None"=None,
        security_policy: "typing.Literal['none', 'basic128rsa15', 'basic256', 'basic256sha256', 'aes128sha256rsaoaep', 'aes256sha256rsapss'] | None"=None,
        timeout: "float | datetime.timedelta | None"=None,
    ))]
    pub fn connect(
        py: Python<'_>,
        endpoint_url: &str, username: Option<&str>,
        password: Option<&str>,
        cert: Option<&[u8]>,
        private_key: Option<&[u8]>,
        ignore_server_cert: bool,
        use_generated_cert: bool,
        security_mode: Option<&str>,
        security_policy: Option<&str>,
        timeout: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let rt = py.detach(|| get_runtime())?;

        let pki = if let Some((cert, private_key)) = require_both!(cert, private_key)? {
            PkiConfig::UseProvided(
                Certificate::from_bytes(cert),
                PrivateKey::from_bytes(private_key),
            )
        } else if use_generated_cert {
            PkiConfig::GenerateSelfSigned
        } else {
            PkiConfig::None
        };

        // fail fast if arguments aren't well-formed
        let maybe_basic_auth = require_both!(username, password)?;

        let mut builder = ClientBuilder::new(pki).map_err(|e| {
            PyValueError::new_err(format!("failed to initialize a client builder: '{e}'"))
        })?;

        if let Some((username, password)) = maybe_basic_auth {
            let user_str = ua::String::new(username)
                .map_err(|_| PyValueError::new_err(format!("invalid username: '{username}'")))?;

            let user_token = ua::UserNameIdentityToken::init()
                .with_password(ua::ByteString::new(password.as_bytes()))
                .with_user_name(user_str);

            builder = builder.with_identity_token(&ua::UserIdentityToken::UserName(user_token));
        }

        if let Some(timeout) = timeout {
            let timeout = if let Ok(timeout_float) = timeout.extract::<f64>() {
                Duration::from_secs_f64(timeout_float)
            } else if let Ok(timeout_duration) = timeout.extract::<Duration>() {
                timeout_duration
            } else {
                return Err(PyValueError::new_err(format!(
                    "invalid timeout: '{timeout}'"
                )));
            };

            builder = builder.with_timeout(timeout);
        }

        if let Some(security_mode) = security_mode {
            let security_mode = parse_security_mode(security_mode)?;
            builder = builder.with_security_mode(security_mode);
        }

        if let Some(security_policy) = security_policy {
            let security_policy = parse_security_policy(security_policy)?;
            builder = builder.with_security_policy(security_policy);
        }

        if ignore_server_cert {
            builder = builder.with_trust_server_certs();
        }

        let client = builder.connect(endpoint_url).map_err(|e| {
            PyValueError::new_err(format!(
                "failed to connect to endpoint with url '{endpoint_url}': '{e}'"
            ))
        })?;

        Ok(Self { client, rt })
    }

    pub fn disconnect(&mut self, py: Python<'_>) {
        py.detach(|| {
            self.rt.block_on(self.client.disconnect());
        });
    }

    #[pyo3(signature = (
        on_data: "typing.Callable[[list[tuple[NodeId, DataPoint]]], None]",
        nodes,
        freq_hz,
        bg_poll_freq_hz=None,
        lifetime_count=None,
        max_keep_alive_count=None,
        max_notifications_per_publish=None,
        priority=None,
        publish_freq_hz=None,
        queue_size=None,
        discard_oldest=true,
    ))]
    pub fn subscribe(
        &self,
        py: Python<'_>,
        on_data: Py<PyAny>,
        nodes: Vec<Bound<'_, PyAny>>,
        freq_hz: f64,
        bg_poll_freq_hz: Option<f64>,
        lifetime_count: Option<u32>,
        max_keep_alive_count: Option<NonZeroU32>,
        max_notifications_per_publish: Option<NonZeroU32>,
        priority: Option<u8>,
        publish_freq_hz: Option<f64>,
        queue_size: Option<u32>,
        discard_oldest: bool,
    ) -> PyResult<Session> {
        if !on_data.bind(py).is_callable() {
            return Err(PyValueError::new_err("`on_data` must be callable"));
        }

        let nodes = nodes
            .iter()
            .map(|item| NodeIdKind::extract(item.as_borrowed()))
            .collect::<PyResult<Vec<_>>>()?;

        let nodes = py.detach(|| self.parse_nodes(nodes))?;

        let sample_rate = duration_from_freq(freq_hz)?;

        let publishing_interval = publish_freq_hz
            .map(duration_from_freq)
            .transpose()?
            .unwrap_or(sample_rate);

        #[expect(clippy::unwrap_used)]
        let sub_config = SubscriptionConfig {
            publishing_enabled: true,
            background_poll_interval: bg_poll_freq_hz.map(duration_from_freq).transpose()?,
            lifetime_count: lifetime_count.unwrap_or(10),
            max_keep_alive_count: max_keep_alive_count.unwrap_or(NonZeroU32::new(10).unwrap()),
            max_notifications_per_publish: max_notifications_per_publish
                .unwrap_or(NonZeroU32::new(u32::MAX).unwrap()),
            priority: priority.unwrap_or(0),
            publishing_interval,
        };

        let item_config = SubscriptionItemConfig {
            sampling_interval: sample_rate,
            queue_size: queue_size.unwrap_or(100),
            discard_oldest,
        };

        let on_data = move |points: Box<dyn Iterator<Item = _>>| {
            Python::attach(|py| {
                let results = points
                    .map(|(node, value)| {
                        let node_id = NodeId(node).into_bound_py_any(py)?;
                        let data_point = DataPoint(value).into_bound_py_any(py)?;

                        PyTuple::new(py, [node_id, data_point])
                    })
                    .collect::<PyResult<Vec<_>>>()
                    .and_then(|points| points.into_bound_py_any(py))?;

                on_data.call1(py, (results,))?;

                Ok(())
            })
        };

        let session = py.detach(|| {
            self
                .rt
                .block_on(
                    self.client
                        .start_subscription(&nodes, sub_config, item_config, on_data),
                )
                .map_err(|e| {
                    PyRuntimeError::new_err(format!("failed to start subscription: '{e}'"))
                })
        })?;

        Ok(Session::new(self, session))
    }

    pub fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    pub fn __exit__<'py>(
        &mut self,
        py: Python<'py>,
        _exc_type: Option<Bound<'py, PyAny>>,
        _exc_value: Option<Bound<'py, PyAny>>,
        _traceback: Option<Bound<'py, PyAny>>,
    ) -> PyResult<bool> {
        self.disconnect(py);
        Ok(false)
    }
}

fn duration_from_freq(f: f64) -> PyResult<Duration> {
    if f.is_finite() && f > 0.0 {
        Ok(Duration::from_secs_f64(f.recip()))
    } else {
        Err(PyValueError::new_err(format!("frequency must be finite and positive: '{f}'")))
    }
}

fn parse_security_mode(security_mode: &str) -> PyResult<ua::MessageSecurityMode> {
    Ok(match security_mode.to_lowercase().as_str() {
        "none" => ua::MessageSecurityMode::NONE,
        "sign" => ua::MessageSecurityMode::SIGN,
        "sign_and_encrypt" => ua::MessageSecurityMode::SIGNANDENCRYPT,
        _ => {
            return Err(PyValueError::new_err(format!(
                "invalid security mode: '{security_mode}'"
            )));
        }
    })
}

fn parse_security_policy(security_policy: &str) -> PyResult<SecurityPolicyUri> {
    Ok(match security_policy.to_lowercase().as_str() {
        "none" => SecurityPolicyUri::None,
        "basic128rsa15" => SecurityPolicyUri::Basic128Rsa15,
        "basic256" => SecurityPolicyUri::Basic256,
        "basic256sha256" => SecurityPolicyUri::Basic256Sha256,
        "aes128sha256rsaoaep" => SecurityPolicyUri::Aes128Sha256RsaOaep,
        "aes256sha256rsapss" => SecurityPolicyUri::Aes256Sha256RsaPss,
        _ => {
            return security_policy.parse::<SecurityPolicyUri>().map_err(|_| {
                PyValueError::new_err(format!("invalid security policy: '{security_policy}'"))
            });
        }
    })
}
