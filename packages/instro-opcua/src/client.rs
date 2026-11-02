mod shared_rt;

use std::num::NonZeroU32;
use std::ops::Deref;
use std::str::FromStr;
use std::sync::LazyLock;
use std::time::Duration;

use instro_opcua::open62541::Certificate;
use instro_opcua::open62541::PrivateKey;
use instro_opcua::prelude::*;
use itertools::Either;
use itertools::Itertools;
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyRuntimeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyString;
use tokio::runtime::Runtime;

use self::shared_rt::get_runtime;
use crate::require_both;
use crate::types::AttributeId;
use crate::types::BrowsePath;
use crate::types::DataValue;
use crate::types::MessageSecurityMode;
use crate::types::NodeId;
use crate::types::QualifiedName;
use crate::types::RelativePath;
use crate::util::RuntimeExt as _;
use crate::util::py_match;

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
        self.0.unblock_on(py, async {
            if let Some(session) = self.1.take() {
                session.stop().await
            }
        })?;

        Ok(true)
    }
}

impl Session {
    fn new(client: &OpcUaClient, session: StreamSession) -> Self {
        Self(client.rt, Some(session))
    }
}

#[pyclass]
pub struct OpcUaClient {
    client: Client,
    rt: &'static Runtime,
}

impl Deref for OpcUaClient {
    type Target = Client;
    fn deref(&self) -> &Self::Target {
        &self.client
    }
}

enum NodeIdKind {
    NodeId(ua::NodeId),
    Path(ua::BrowsePath),
}

impl FromPyObject<'_, '_> for NodeIdKind {
    type Error = PyErr;

    fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
        Ok(py_match! {
            id: NodeId = obj => NodeIdKind::NodeId(id.borrow().as_ua_ref().clone()),

            #[extract]
            id_str: &str = obj => Self::from_str(id_str)?,

            path: RelativePath = obj => {
                let path = ua::BrowsePath::init()
                    .with_relative_path(path.borrow().as_ua_ref().clone())
                    .with_starting_node(ROOT_FOLDER_ID.clone());

                NodeIdKind::Path(path)
            },

            else => {
                Err(PyValueError::new_err(format!(
                    "invalid node id or path: '{}'",
                    obj.as_any()
                )))?
            }
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
                .with_relative_path(path)
                .with_starting_node(ROOT_FOLDER_ID.clone());

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
        paths: ua::Array<ua::BrowsePath>,
    ) -> PyResult<Vec<(ua::BrowsePath, ua::NodeId)>> {
        let request =
            ua::TranslateBrowsePathsToNodeIdsRequest::init().with_browse_paths(paths.clone());

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
            results
                .iter()
                .zip(paths.iter())
                .partition_map(|(result, path)| {
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
                            return Either::Left((
                                path.clone(),
                                targets[0].target_id().node_id().clone(),
                            ));
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

        if paths.is_empty() {
            return Ok(ids);
        }

        Ok(self
            .rt
            .block_on(self.translate_browse_paths(ua::Array::from_slice(&paths)))?
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
        security_mode: "typing.Literal['none', 'sign', 'signandencrypt'] | None"=None,
        security_policy: "typing.Literal['none', 'basic128rsa15', 'basic256', 'basic256sha256', 'aes128sha256rsaoaep', 'aes256sha256rsapss'] | None"=None,
        timeout: "float | datetime.timedelta | None"=None,
    ))]
    pub fn connect(
        py: Python<'_>,
        endpoint_url: &str,
        username: Option<&str>,
        password: Option<&str>,
        cert: Option<&[u8]>,
        private_key: Option<&[u8]>,
        ignore_server_cert: bool,
        use_generated_cert: bool,
        security_mode: Option<Bound<'_, PyAny>>,
        security_policy: Option<&str>,
        timeout: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let rt = py.detach(get_runtime)?;

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
                duration_from_timeout_secs(timeout_float)?
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
            let security_mode = security_mode.extract::<MessageSecurityMode>()?;
            builder = builder.with_security_mode(security_mode.as_ua());
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

    pub fn disconnect(&mut self, py: Python<'_>) -> PyResult<()> {
        self.rt
            .unblock_on(py, async { self.client.disconnect().await })
    }

    #[pyo3(signature = (
        on_data: "typing.Callable[[list[tuple[NodeId, DataValue]]], None]",
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
                        let node_id = NodeId::new(node).into_bound_py_any(py)?;
                        let data_point = DataValue::new(value).into_bound_py_any(py)?;

                        Ok((node_id, data_point))
                    })
                    .collect::<PyResult<Vec<_>>>()?;

                if let Err(e) = on_data.call1(py, (results,)) {
                    e.write_unraisable(py, None);
                }

                Ok(())
            })
        };

        let session = self.rt.unblock_on(py, async {
            self.client
                .start_subscription(&nodes, sub_config, item_config, on_data)
                .await
                .map_err(|e| {
                    PyRuntimeError::new_err(format!("failed to start subscription: '{e}'"))
                })
        })??;

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
        self.disconnect(py)?;
        Ok(false)
    }

    #[getter]
    pub fn objects(&self) -> Node {
        Node::objects(self.clone())
    }

    #[getter]
    pub fn views(&self) -> Node {
        Node::views(self.clone())
    }

    #[getter]
    pub fn types(&self) -> Node {
        Node::types(self.clone())
    }

    #[pyo3(signature = (path: "types.RelativePath | str") -> "Node")]
    pub fn get_node(&self, py: Python<'_>, path: Bound<'_, PyAny>) -> PyResult<Node> {
        let path = py_match! {
            path: RelativePath = path => path,

            #[extract]
            path: &str = path => Bound::new(py, RelativePath::parse(path)?)?,

            else => {
                Err(PyValueError::new_err(format!("invalid path: '{path}'")))?
            }
        }
        .borrow()
        .as_ua_ref()
        .clone();

        self.rt.unblock_on(py, async {
            let path = ua::BrowsePath::init()
                .with_relative_path(&path)
                .with_starting_node(&ROOT_FOLDER_ID);

            #[expect(
                clippy::unwrap_used,
                reason = "`translate_browse_paths` verifies that the len(input) == len(output)"
            )]
            let (browse_path, node_id) = self
                .translate_browse_paths(vec![path])
                .await?
                .into_iter()
                .next()
                .unwrap();

            Ok(Node {
                client: self.clone(),
                node_id,
                browse_path,
            })
        })?
    }
}

fn duration_from_timeout_secs(seconds: f64) -> PyResult<Duration> {
    Duration::try_from_secs_f64(seconds).map_err(|_| {
        PyValueError::new_err(format!(
            "timeout must be finite, nonnegative, and within the supported duration range: \
             '{seconds}'"
        ))
    })
}

fn duration_from_freq(f: f64) -> PyResult<Duration> {
    if f.is_finite() && f > 0.0 {
        Duration::try_from_secs_f64(f.recip()).map_err(|_| {
            PyValueError::new_err(format!(
                "frequency produces an interval outside the supported duration range: '{f}'"
            ))
        })
    } else {
        Err(PyValueError::new_err(format!(
            "frequency must be finite and positive: '{f}'"
        )))
    }
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
            // also accept the full URI string, if they wanna be extra about it
            return security_policy.parse::<SecurityPolicyUri>().map_err(|_| {
                PyValueError::new_err(format!("invalid security policy: '{security_policy}'"))
            });
        }
    })
}

#[pyclass]
pub struct Node {
    client: OpcUaClient,
    node_id: ua::NodeId,
    browse_path: ua::BrowsePath,
}

static ROOT_FOLDER_ID: LazyLock<ua::NodeId> =
    LazyLock::new(|| ua::NodeId::ns0(open62541_sys::UA_NS0ID_ROOTFOLDER));

impl Node {
    fn well_known(client: OpcUaClient, id: u32, path: &str) -> Self {
        Self {
            client,
            node_id: ua::NodeId::ns0(id),
            browse_path: ua::BrowsePath::init()
                .with_starting_node(&ROOT_FOLDER_ID)
                .with_relative_path(&ua::RelativePath::parse(path).unwrap()),
        }
    }

    fn objects(client: OpcUaClient) -> Self {
        Self::well_known(client, open62541_sys::UA_NS0ID_OBJECTSFOLDER, "/Objects")
    }

    fn views(client: OpcUaClient) -> Self {
        Self::well_known(client, open62541_sys::UA_NS0ID_VIEWSFOLDER, "/Views")
    }

    fn types(client: OpcUaClient) -> Self {
        Self::well_known(client, open62541_sys::UA_NS0ID_TYPESFOLDER, "/Types")
    }
}

#[pymethods]
impl Node {
    #[getter]
    pub fn node_id(&self) -> NodeId {
        NodeId::new(self.node_id.clone())
    }

    #[getter]
    pub fn browse_path(&self) -> BrowsePath {
        BrowsePath::new(self.browse_path.clone())
    }

    #[pyo3(signature = (path: "types.RelativePath | str") -> "Node")]
    pub fn get_child(&self, py: Python<'_>, path: Bound<'_, PyAny>) -> PyResult<Self> {
        let child_path = py_match! {
            #[extract]
            path: RelativePath = path => path,

            #[extract]
            path: &str = path => RelativePath::parse(path)?,

            else => {
                Err(PyValueError::new_err(format!("invalid path: '{path}'")))?
            }
        };

        if child_path.is_empty() {
            Err(PyValueError::new_err(format!(
                "invalid relative path: '{path}'"
            )))?;
        }

        self.client.rt.unblock_on(py, async {
            // scope the browse path translation to the parent
            let path = ua::BrowsePath::init()
                .with_starting_node(self.node_id().as_ua_ref())
                .with_relative_path(
                    #[expect(
                        clippy::unwrap_used,
                        reason = "`child_path.is_empty()` is checked above"
                    )]
                    &ua::RelativePath::init().with_elements(child_path.elements().unwrap()),
                );

            #[expect(
                clippy::unwrap_used,
                reason = "`translate_browse_paths` verifies that the len(input) == len(output)"
            )]
            let (_, node_id) = self
                .client
                .translate_browse_paths(vec![path])
                .await?
                .into_iter()
                .next()
                .unwrap();

            let all_elements = self
                .browse_path
                .relative_path()
                .iter()
                .chain(child_path.iter())
                .cloned()
                .collect_vec();

            let path = ua::RelativePath::init().with_elements(all_elements.as_slice());

            // normalize the path to be rooted at the root folder
            let browse_path = ua::BrowsePath::init()
                .with_starting_node(&ROOT_FOLDER_ID)
                .with_relative_path(&path);

            Ok(Self {
                client: self.client.clone(),
                node_id,
                browse_path,
            })
        })?
    }

    #[getter]
    pub fn children(&self, py: Python<'_>) -> PyResult<Vec<Self>> {
        self.client.rt.unblock_on(py, async {
            let my_path = self.browse_path.relative_path().elements().unwrap_or(&[]);

            static HIERARCHICAL_NODE_ID: LazyLock<ua::NodeId> =
                LazyLock::new(|| ua::NodeId::ns0(open62541_sys::UA_NS0ID_HIERARCHICALREFERENCES));

            let desc = ua::BrowseDescription::init()
                .with_browse_direction(&ua::BrowseDirection::FORWARD)
                .with_include_subtypes(true)
                .with_node_id(&self.node_id)
                .with_reference_type_id(&HIERARCHICAL_NODE_ID)
                .with_result_mask(&ua::BrowseResultMask::ALL);

            let results = self
                .client
                .with_client(async |client| -> PyResult<Vec<ua::ReferenceDescription>> {
                    let (mut results, mut cont_point) =
                        client.browse(&desc).await.map_err(|e| {
                            PyValueError::new_err(format!("failed to browse children: '{e}'"))
                        })?;

                    while let Some(this_point) = cont_point {
                        let next_results =
                            client.browse_next(&[this_point]).await.map_err(|e| {
                                PyValueError::new_err(format!(
                                    "failed to browse next children: '{e}'"
                                ))
                            })?;

                        if next_results.len() != 1 {
                            Err(PyValueError::new_err(format!(
                                "expected 1 browse result, got {}",
                                results.len()
                            )))?;
                        }

                        #[expect(
                            clippy::unwrap_used,
                            reason = "`next_results.len() == 1` is checked above"
                        )]
                        let (next_results, next_point) =
                            next_results.into_iter().next().unwrap().map_err(|e| {
                                PyValueError::new_err(format!(
                                    "browsing the continuation point failed: '{e}'"
                                ))
                            })?;

                        results.extend(next_results);

                        cont_point = next_point;
                    }

                    Ok(results)
                })
                .await
                .map_err(|e| {
                    PyRuntimeError::new_err(format!("client was disconnected: '{e}'"))
                })??;

            Ok(results
                .into_iter()
                .filter(|reference| reference.node_id().server_index() == 0)
                .map(|reference| {
                    let element = ua::RelativePathElement::init()
                        .with_include_subtypes(true)
                        .with_reference_type_id(&HIERARCHICAL_NODE_ID)
                        .with_is_inverse(false)
                        .with_target_name(reference.browse_name());

                    let child_path_items = my_path.iter().cloned().chain([element]).collect_vec();

                    let child_path =
                        ua::RelativePath::init().with_elements(child_path_items.as_slice());

                    let browse_path = ua::BrowsePath::init()
                        .with_relative_path(&child_path)
                        // this should always be the Root folder
                        .with_starting_node(self.browse_path.starting_node());

                    Self {
                        client: self.client.clone(),
                        node_id: reference.node_id().node_id().clone(),
                        browse_path,
                    }
                })
                .collect_vec())
        })?
    }

    #[pyo3(name = "get_attribute")]
    pub fn get_attribute_py(&self, py: Python<'_>, attr: AttributeId) -> PyResult<DataValue> {
        self.client.rt.unblock_on(py, async {
            let result = self
                .client
                .read_node(&self.node_id, &attr.as_ua())
                .await
                .map_err(|e| PyValueError::new_err(format!("failed to read attribute: '{e}'")))?;

            Ok(DataValue::new(result))
        })?
    }

    #[getter]
    pub fn value(&self, py: Python<'_>) -> PyResult<DataValue> {
        self.get_attribute_py(py, AttributeId::VALUE)
    }

    #[getter]
    pub fn display_name(&self, py: Python<'_>) -> PyResult<String> {
        self.get_attribute(py, ua::AttributeId::DISPLAYNAME_T)
            .and_then(normalize_data_value)
            .map(|text| text.text().to_string())
    }

    #[getter]
    pub fn description(&self, py: Python<'_>) -> PyResult<String> {
        self.get_attribute(py, ua::AttributeId::DESCRIPTION_T)
            .and_then(normalize_data_value)
            .map(|text| text.text().to_string())
    }

    #[getter]
    pub fn browse_name(&self, py: Python<'_>) -> PyResult<QualifiedName> {
        self.get_attribute(py, ua::AttributeId::BROWSENAME_T)
            .and_then(normalize_data_value)
            .map(QualifiedName::new)
    }

    pub fn __repr__(&self) -> String {
        format!(
            "<Node node_id='{}' browse_path={}>",
            *self.node_id(),
            self.browse_path().__repr__()
        )
    }
}

fn normalize_data_value<T: open62541::DataType>(value: open62541::DataValue<T>) -> PyResult<T> {
    match value.into_scalar_value() {
        Some(scalar) => Ok(scalar),
        None => Err(PyValueError::new_err(
            "querying attribute returned an invalid value",
        )),
    }
}

impl Node {
    pub fn get_attribute<A: open62541::Attribute + Send + Sync>(
        &self,
        py: Python<'_>,
        attr: A,
    ) -> PyResult<open62541::DataValue<A::Value>> {
        self.client.rt.unblock_on(py, async {
            self.client
                .read_node(&self.node_id, attr)
                .await
                .map_err(|e| PyValueError::new_err(format!("failed to read attribute: '{e}'")))
        })?
    }
}

#[pyclass]
pub struct Node {
    client: OpcUaClient,
    node_id: ua::NodeId,
    browse_path: ua::BrowsePath,
}

static ROOT_FOLDER_ID: LazyLock<ua::NodeId> = LazyLock::new(|| ua::NodeId::ns0(open62541_sys::UA_NS0ID_ROOTFOLDER));

impl Node {
    fn well_known(client: OpcUaClient, id: u32, path: &str) -> Self {
        Self {
            client,
            node_id: ua::NodeId::ns0(id),
            browse_path: ua::BrowsePath::init()
                .with_starting_node(&ROOT_FOLDER_ID)
                .with_relative_path(&ua::RelativePath::parse(path).unwrap()),
        }
    }

    fn objects(client: OpcUaClient) -> Self {
        Self::well_known(client, open62541_sys::UA_NS0ID_OBJECTSFOLDER, "/Objects")
    }

    fn views(client: OpcUaClient) -> Self {
        Self::well_known(client, open62541_sys::UA_NS0ID_VIEWSFOLDER, "/Views")
    }

    fn types(client: OpcUaClient) -> Self {
        Self::well_known(client, open62541_sys::UA_NS0ID_TYPESFOLDER, "/Types")
    }
}

#[pymethods]
impl Node {
    #[getter]
    pub fn node_id(&self) -> NodeId {
        NodeId::new(self.node_id.clone())
    }

    #[getter]
    pub fn browse_path(&self) -> BrowsePath {
        BrowsePath::new(self.browse_path.clone())
    }

    pub fn get_child(&self, py: Python<'_>, path: &str) -> PyResult<Self> {
        self.client.rt.unblock_on(py, async || {
            let child_path: ua::RelativePath = path
                .parse()
                .map_err(|e| PyValueError::new_err(format!("invalid relative path: '{e}'")))?;

            let all_elements = self.browse_path
                .relative_path()
                .iter()
                .chain(child_path.iter())
                .cloned()
                .collect_vec();

            let path = ua::BrowsePath::init()
                .with_starting_node(&ROOT_FOLDER_ID)
                .with_relative_path(&ua::RelativePath::init().with_elements(all_elements.as_slice()));

            #[expect(clippy::unwrap_used, reason = "`translate_browse_paths` verifies that the len(input) == len(output)")]
            let (browse_path, node_id) = self.client
                .translate_browse_paths(vec![path])
                .await?
                .into_iter()
                .next()
                .unwrap();

            Ok(Self {
                client: self.client.clone(),
                node_id,
                browse_path,
            })
        })
    }

    #[getter]
    pub fn children(&self, py: Python<'_>) -> PyResult<Vec<Self>> {
        self.client.rt.unblock_on(py, async || {
            let my_path = self.browse_path.relative_path().elements().unwrap_or(&[]);

            static HIERARCHICAL_NODE_ID: LazyLock<ua::NodeId> = LazyLock::new(|| ua::NodeId::ns0(open62541_sys::UA_NS0ID_HIERARCHICALREFERENCES));

            let desc = ua::BrowseDescription::init()
                .with_browse_direction(&ua::BrowseDirection::FORWARD)
                .with_include_subtypes(true)
                .with_node_id(&self.node_id)
                .with_reference_type_id(&HIERARCHICAL_NODE_ID)
                .with_result_mask(&ua::BrowseResultMask::ALL);

            let results = self.client.with_client(async |client| -> PyResult<Vec<ua::ReferenceDescription>> {
                let (mut results, mut cont_point) = client
                    .browse(&desc)
                    .await
                    .map_err(|e| PyValueError::new_err(format!("failed to browse children: '{e}'")))?;

                while let Some(this_point) = cont_point {
                    let next_results = client
                        .browse_next(&[this_point])
                        .await
                        .map_err(|e| PyValueError::new_err(format!("failed to browse next children: '{e}'")))?;

                    if next_results.len() != 1 {
                        Err(PyValueError::new_err(format!("expected 1 browse result, got {}", results.len())))?;
                    }

                    #[expect(clippy::unwrap_used, reason = "`next_results.len() == 1` is checked above")]
                    let (next_results, next_point) = next_results
                        .into_iter()
                        .next()
                        .unwrap()
                        .map_err(|e| PyValueError::new_err(format!("browsing the continuation point failed: '{e}'")))?;

                    results.extend(next_results);

                    cont_point = next_point;
                }

                Ok(results)
            })
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("client was disconnected: '{e}'")))??;

            Ok(
                results
                    .into_iter()
                    .filter(|reference| reference.node_id().server_index() == 0)
                    .map(|reference| {
                        let element = ua::RelativePathElement::init()
                            .with_include_subtypes(true)
                            .with_reference_type_id(&HIERARCHICAL_NODE_ID)
                            .with_is_inverse(false)
                            .with_target_name(reference.browse_name());

                        let child_path_items = my_path
                            .iter()
                            .cloned()
                            .chain([element])
                            .collect_vec();

                        let child_path = ua::RelativePath::init()
                            .with_elements(child_path_items.as_slice());

                        let browse_path = ua::BrowsePath::init()
                            .with_relative_path(&child_path)
                            // this should always be the Root folder
                            .with_starting_node(self.browse_path.starting_node());

                        Self {
                            client: self.client.clone(),
                            node_id: reference.node_id().node_id().clone(),
                            browse_path,
                        }
                    })
                    .collect_vec()
            )
        })
    }

    #[pyo3(name = "get_attribute")]
    pub fn get_attribute_py(&self, py: Python<'_>, attr: AttributeId) -> PyResult<DataValue> {
        self.client.rt.unblock_on(py, async || {
            let result = self.client
                .read_node(&self.node_id, &attr.as_ua())
                .await
                .map_err(|e| PyValueError::new_err(format!("failed to read attribute: '{e}'")))?;

            Ok(DataValue::new(result))
        })
    }

    #[getter]
    pub fn value(&self, py: Python<'_>) -> PyResult<DataValue> {
        self.get_attribute_py(py, AttributeId::VALUE)
    }

    #[getter]
    pub fn display_name(&self, py: Python<'_>) -> PyResult<String> {
        self.get_attribute(py, ua::AttributeId::DISPLAYNAME_T)
            .and_then(normalize_data_value)
            .map(|text| text.text().to_string())
    }

    #[getter]
    pub fn description(&self, py: Python<'_>) -> PyResult<String> {
        self.get_attribute(py, ua::AttributeId::DESCRIPTION_T)
            .and_then(normalize_data_value)
            .map(|text| text.text().to_string())
    }

    #[getter]
    pub fn browse_name(&self, py: Python<'_>) -> PyResult<QualifiedName> {
        self.get_attribute(py, ua::AttributeId::BROWSENAME_T)
            .and_then(normalize_data_value)
            .map(QualifiedName::new)
    }

    pub fn __repr__(&self) -> String {
        format!("<Node node_id='{}' browse_path={}>", self.node_id().to_string(), self.browse_path().__repr__())
    }
}

fn normalize_data_value<T: open62541::DataType>(value: open62541::DataValue<T>) -> PyResult<T> {
    match value.into_scalar_value() {
        Some(scalar) => Ok(scalar),
        None => Err(PyValueError::new_err("querying attribute returned an invalid value")),
    }
}

impl Node {
    pub fn get_attribute<A: open62541::Attribute + Send + Sync>(&self, py: Python<'_>, attr: A) -> PyResult<open62541::DataValue<A::Value>> {
        self.client.rt.unblock_on(py, async || {
            self.client
                .read_node(&self.node_id, attr)
                .await
                .map_err(|e| PyValueError::new_err(format!("failed to read attribute: '{e}'")))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn node_id_identifiers_preserve_python_types_and_values() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let uuid = "12345678-1234-5678-9abc-def012345678";
            let cases = [
                ("ns=2;i=42", 42.into_bound_py_any(py)?),
                ("ns=2;s=pump", "pump".into_bound_py_any(py)?),
                (
                    "ns=2;b=AP8=",
                    pyo3::types::PyBytes::new(py, &[0, 255]).into_any(),
                ),
                (
                    "ns=2;g=12345678-1234-5678-9abc-def012345678",
                    py.import("uuid")?.getattr("UUID")?.call1((uuid,))?,
                ),
            ];

            for (node_id, expected) in cases {
                let node = NodeId::from_string(node_id)?;

                let identifier = node.identifier(py)?;

                assert!(identifier.get_type().is(expected.get_type()));
                assert!(identifier.eq(expected)?);
            }
            Ok(())
        })
    }

    #[test]
    fn invalid_timeouts_return_errors_instead_of_panicking() {
        assert!(duration_from_timeout_secs(-1.0).is_err());
        assert!(duration_from_timeout_secs(f64::NAN).is_err());
        assert!(duration_from_timeout_secs(f64::MAX).is_err());
    }

    #[test]
    fn frequency_is_converted_to_period_without_panicking_on_invalid_intervals() {
        assert_eq!(
            duration_from_freq(100.0).unwrap(),
            Duration::from_millis(10)
        );
        assert!(duration_from_freq(0.0).is_err());
        let frequency_with_unrepresentable_period = 1e-20;
        assert!(duration_from_freq(frequency_with_unrepresentable_period).is_err());
    }
}
