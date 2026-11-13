use std::future::Future;
use std::str::FromStr;
use std::sync::Arc;
use std::sync::OnceLock;
use std::time::Duration;

use opcua::client::Session;
use opcua::client::SessionDropGuard;
use opcua::crypto::SecurityPolicy;
use opcua::types as ua;
use pyo3::exceptions::PyBaseException;
use pyo3::exceptions::PyRuntimeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyAny;
use pyo3::types::PyDict;
use tokio::runtime::Runtime;
use tokio::sync::watch;

use crate::config::IdentityToken;
use crate::config::{self};
use crate::error;
use crate::subscription::MonitoredItemSettings;
use crate::subscription::Settings;
use crate::subscription::Subscription;
use crate::subscription::{self};
use crate::values::AttributeId;
use crate::values::BrowsePath;
use crate::values::DataValue;
use crate::values::NodeId;
use crate::values::RelativePath;

pub fn runtime() -> PyResult<&'static Runtime> {
    static RUNTIME: OnceLock<Result<Runtime, String>> = OnceLock::new();
    RUNTIME
        .get_or_init(|| {
            tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .thread_name("instro-opcua")
                .build()
                .map_err(|error| error.to_string())
        })
        .as_ref()
        .map_err(|error| PyRuntimeError::new_err(error.clone()))
}

pub fn wait<T: Send + 'static>(
    py: Python<'_>,
    future: impl Future<Output = PyResult<T>> + Send + 'static,
) -> PyResult<T> {
    let runtime = runtime()?;
    let task = runtime.spawn(future);
    while !task.is_finished() {
        py.detach(|| std::thread::sleep(Duration::from_millis(10)));
        if let Err(error) = py.check_signals() {
            task.abort();
            return Err(error);
        }
    }
    py.detach(|| runtime.block_on(task))
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
}

pub struct Connection {
    pub session: Arc<Session>,
    pub completion: watch::Receiver<Option<ua::StatusCode>>,
    _guard: SessionDropGuard,
}

#[pyclass(frozen, module = "instro.unstable._opcua")]
pub struct OpcUaClient {
    pub connection: Arc<Connection>,
}

#[pymethods]
impl OpcUaClient {
    #[staticmethod]
    #[pyo3(signature = (endpoint_url=None, *, identity=None, security_policy="None", security_mode=1, **settings))]
    fn connect(
        py: Python<'_>,
        endpoint_url: Option<String>,
        identity: Option<IdentityToken>,
        security_policy: &str,
        security_mode: u32,
        settings: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Self> {
        let mut client = config::builder(settings)?
            .client()
            .map_err(|errors| PyValueError::new_err(errors.join("; ")))?;
        let policy = SecurityPolicy::from_str(security_policy).map_err(|()| {
            PyValueError::new_err(format!("unknown security policy: {security_policy}"))
        })?;
        let mode = ua::MessageSecurityMode::try_from(security_mode as i32)
            .map_err(|_| PyValueError::new_err("invalid security_mode"))?;
        let identity = identity.map(|identity| identity.0);

        wait(py, async move {
            let (session, driver) = match endpoint_url {
                Some(url) => {
                    client
                        .connect_to_matching_endpoint(
                            ua::EndpointDescription {
                                endpoint_url: url.into(),
                                security_policy_uri: policy.to_uri().into(),
                                security_mode: mode,
                                ..Default::default()
                            },
                            identity.unwrap_or(opcua::client::IdentityToken::Anonymous),
                        )
                        .await
                }
                None => {
                    let endpoints = client.get_server_endpoints().await.map_err(error::native)?;
                    let builder = client
                        .session_builder()
                        .with_endpoints(endpoints)
                        .connect_to_default_endpoint()
                        .map_err(error::native)?;
                    let builder = match identity {
                        Some(identity) => builder.user_identity_token(identity),
                        None => builder,
                    };
                    builder.build(client.certificate_store().clone())
                }
            }
            .map_err(error::native)?;
            let guard = session.close_on_drop();
            let (finished, mut completion) = watch::channel(None);
            tokio::spawn(async move {
                let status = driver.run().await;
                let _ = finished.send(Some(status));
            });
            tokio::select! {
                connected = session.wait_for_connection() => {
                    if !connected { return Err(PyRuntimeError::new_err("native connection ended before activation")); }
                }
                _ = completion.changed() => {
                    let status = (*completion.borrow()).ok_or_else(|| PyRuntimeError::new_err("native session task ended"))?;
                    return Err(error::status(status, "native session ended before activation"));
                }
            }
            Ok(Self {
                connection: Arc::new(Connection {
                    session,
                    completion,
                    _guard: guard,
                }),
            })
        })
    }

    fn disconnect(&self, py: Python<'_>) -> PyResult<()> {
        let connection = self.connection.clone();
        wait(py, async move {
            connection.session.disconnect().await.map_err(error::native)
        })
    }

    fn enable_reconnects(&self) {
        self.connection.session.enable_reconnects();
    }

    fn disable_reconnects(&self) {
        self.connection.session.disable_reconnects();
    }

    #[pyo3(signature = (nodes, *, attribute, publishing_interval=None, lifetime_count=60, max_keep_alive_count=20, max_notifications_per_publish=0, priority=0, publishing_enabled=true, sampling_interval=0.0, queue_size=0, discard_oldest=false, poll_interval=None, item_settings=None))]
    #[allow(clippy::too_many_arguments)]
    fn subscribe(
        &self,
        py: Python<'_>,
        nodes: Vec<Node>,
        attribute: AttributeId,
        publishing_interval: Option<crate::time::Duration>,
        lifetime_count: u32,
        max_keep_alive_count: u32,
        max_notifications_per_publish: u32,
        priority: u8,
        publishing_enabled: bool,
        sampling_interval: f64,
        queue_size: u32,
        discard_oldest: bool,
        poll_interval: Option<crate::time::Duration>,
        item_settings: Option<Vec<MonitoredItemSettings>>,
    ) -> PyResult<Subscription> {
        let attribute = attribute.0;
        let settings = Settings {
            publishing_interval: publishing_interval
                .map(|value| value.0)
                .unwrap_or(Duration::from_millis(500)),
            lifetime_count,
            max_keep_alive_count,
            max_notifications_per_publish,
            priority,
            publishing_enabled,
            sampling_interval,
            queue_size,
            discard_oldest,
            poll_interval: poll_interval.map(|value| value.0),
        };
        let connection = self.connection.clone();
        wait(
            py,
            subscription::create(connection, nodes, attribute, settings, item_settings),
        )
    }

    fn get_node(&self, py: Python<'_>, path: &Bound<'_, PyAny>) -> PyResult<Node> {
        if let Ok(id) = path.extract::<NodeId>() {
            return Ok(Node::new(self.connection.clone(), id.0));
        }
        Node::new(self.connection.clone(), ua::ObjectId::RootFolder.into()).get_child(py, path)
    }

    #[getter]
    fn objects(&self) -> Node {
        Node::new(self.connection.clone(), ua::ObjectId::ObjectsFolder.into())
    }

    #[getter]
    fn types(&self) -> Node {
        Node::new(self.connection.clone(), ua::ObjectId::TypesFolder.into())
    }

    #[getter]
    fn views(&self) -> Node {
        Node::new(self.connection.clone(), ua::ObjectId::ViewsFolder.into())
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(
        &self,
        py: Python<'_>,
        _kind: &Bound<'_, PyAny>,
        value: Option<&Bound<'_, PyBaseException>>,
        _traceback: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        error::context(py, value, self.disconnect(py))
    }
}

#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct Node {
    pub connection: Arc<Connection>,
    pub id: ua::NodeId,
    path: ua::BrowsePath,
}

impl Node {
    pub fn new(connection: Arc<Connection>, id: ua::NodeId) -> Self {
        let path = ua::BrowsePath {
            starting_node: id.clone(),
            relative_path: Default::default(),
        };
        Self {
            connection,
            id,
            path,
        }
    }
}

macro_rules! node_methods {
    ($($getter:ident => $attribute:ident),* $(,)?) => {
        #[pymethods]
        impl Node {
            #[getter]
            fn node_id(&self) -> NodeId {
                NodeId(self.id.clone())
            }

            #[getter]
            fn browse_path(&self) -> BrowsePath {
                BrowsePath(self.path.clone())
            }

            fn get_attribute(&self, py: Python<'_>, attribute: AttributeId) -> PyResult<DataValue> {
                let attribute = attribute.0;
                let connection = self.connection.clone();
                let id = self.id.clone();
                wait(py, async move {
                    let values = connection
                        .session
                        .read(
                            &[ua::ReadValueId {
                                node_id: id,
                                attribute_id: attribute as u32,
                                ..Default::default()
                            }],
                            ua::TimestampsToReturn::Both,
                            0.0,
                        )
                        .await
                        .map_err(error::native)?;
                    let inner = values
                        .into_iter()
                        .next()
                        .ok_or_else(|| PyRuntimeError::new_err("native read returned no result"))?;
                    Ok(DataValue {
                        inner,
                        attribute: Some(attribute),
                    })
                })
            }

            fn get_child(&self, py: Python<'_>, path: &Bound<'_, PyAny>) -> PyResult<Self> {
                let path = if let Ok(path) = path.extract::<RelativePath>() {
                    path.0
                } else {
                    ua::RelativePath::try_from(path.extract::<String>()?)
                        .map_err(|error| PyValueError::new_err(error.to_string()))?
                };
                if path.elements.as_ref().is_none_or(Vec::is_empty) {
                    return Err(PyValueError::new_err(
                        "relative path must contain a path element",
                    ));
                }
                let connection = self.connection.clone();
                let id = self.id.clone();
                wait(py, async move {
                    let path = ua::BrowsePath {
                        starting_node: id,
                        relative_path: path,
                    };
                    let results = connection
                        .session
                        .translate_browse_paths_to_node_ids(std::slice::from_ref(&path))
                        .await
                        .map_err(error::native)?;
                    let result = results
                        .into_iter()
                        .next()
                        .ok_or_else(|| PyRuntimeError::new_err("native translation returned no result"))?;
                    if !result.status_code.is_good() {
                        return Err(error::status(
                            result.status_code,
                            "relative path translation failed",
                        ));
                    }
                    let [target] = <[ua::BrowsePathTarget; 1]>::try_from(
                        result.targets.unwrap_or_default(),
                    )
                    .map_err(|targets| {
                        PyValueError::new_err(format!(
                            "relative path resolved to {} targets; expected one",
                            targets.len()
                        ))
                    })?;
                    if target.target_id.server_index != 0 || target.remaining_path_index != u32::MAX {
                        return Err(PyValueError::new_err(
                            "relative path did not resolve to a local Node",
                        ));
                    }
                    Ok(Self {
                        connection,
                        id: target.target_id.node_id,
                        path,
                    })
                })
            }

            #[getter]
            fn children(&self, py: Python<'_>) -> PyResult<Vec<Self>> {
                self.browse_children(py, 0)
            }

            #[pyo3(signature = (*, max_references_per_node=0))]
            fn browse_children(&self, py: Python<'_>, max_references_per_node: u32) -> PyResult<Vec<Self>> {
                let connection = self.connection.clone();
                let id = self.id.clone();
                wait(py, async move {
                    let mut results = connection
                        .session
                        .browse(
                            &[ua::BrowseDescription {
                                node_id: id,
                                browse_direction: ua::BrowseDirection::Forward,
                                reference_type_id: ua::ReferenceTypeId::HierarchicalReferences.into(),
                                include_subtypes: true,
                                node_class_mask: 0,
                                result_mask: 63,
                            }],
                            max_references_per_node,
                            None,
                        )
                        .await
                        .map_err(error::native)?;
                    let mut children = Vec::new();
                    loop {
                        let mut continuation_points = Vec::new();
                        for result in results {
                            if !result.status_code.is_good() {
                                return Err(error::status(
                                    result.status_code,
                                    "child enumeration failed",
                                ));
                            }
                            for reference in result.references.into_iter().flatten() {
                                if reference.node_id.server_index != 0 {
                                    return Err(PyValueError::new_err(
                                        "child reference targets another server",
                                    ));
                                }
                                children.push(Self::new(connection.clone(), reference.node_id.node_id));
                            }
                            if !result.continuation_point.is_null_or_empty() {
                                continuation_points.push(result.continuation_point);
                            }
                        }
                        if continuation_points.is_empty() {
                            break;
                        }
                        results = connection
                            .session
                            .browse_next(false, &continuation_points)
                            .await
                            .map_err(error::native)?;
                    }
                    Ok(children)
                })
            }

            fn __repr__(&self) -> String {
                format!("Node({:?})", self.id.to_string())
            }

            $(
                #[getter]
                fn $getter(&self, py: Python<'_>) -> PyResult<DataValue> {
                    self.get_attribute(py, AttributeId(ua::AttributeId::$attribute))
                }
            )*
        }
    };
}

node_methods! {
    node_class => NodeClass, browse_name => BrowseName, display_name => DisplayName,
    description => Description, write_mask => WriteMask, user_write_mask => UserWriteMask,
    is_abstract => IsAbstract, symmetric => Symmetric, inverse_name => InverseName,
    contains_no_loops => ContainsNoLoops, event_notifier => EventNotifier, value => Value,
    data_type => DataType, value_rank => ValueRank, array_dimensions => ArrayDimensions,
    access_level => AccessLevel, user_access_level => UserAccessLevel,
    minimum_sampling_interval => MinimumSamplingInterval, historizing => Historizing,
    executable => Executable, user_executable => UserExecutable,
    data_type_definition => DataTypeDefinition, role_permissions => RolePermissions,
    user_role_permissions => UserRolePermissions, access_restrictions => AccessRestrictions,
    access_level_ex => AccessLevelEx,
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<OpcUaClient>()?;
    module.add_class::<Node>()?;
    Ok(())
}
