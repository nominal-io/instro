use std::ffi::CString;

use opcua::types as ua;
use pyo3::exceptions::PyRuntimeWarning;
use pyo3::exceptions::PyTypeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::client::OpcUaClient;
use crate::config::NativeInteger;
use crate::error;
use crate::util;
use crate::values::AttributeId;
use crate::values::BrowsePath;
use crate::values::NodeId;
use crate::values::PyAttributeValue;
use crate::values::RelativePath;
use crate::values::UaType;
use crate::values::attributes::{self};

pyo3::import_exception!(builtins, ExceptionGroup);

pub(crate) enum NodeInput {
    Node(Node),
    Path(ua::RelativePath),
}

impl NodeInput {
    pub(crate) fn materialize(
        client: &OpcUaClient,
        iterable: &Bound<'_, PyAny>,
    ) -> PyResult<Vec<Self>> {
        let mut nodes = Vec::new();
        for item in iterable.try_iter()? {
            let item = item?;

            if let Ok(node) = item.extract::<Node>() {
                if !client.same_client(node.client()) {
                    return Err(PyValueError::new_err(
                        "all Nodes must belong to this client",
                    ));
                }

                nodes.push(Self::Node(node));
            } else {
                nodes.push(Self::Path(extract_relative_path(item)?));
            }
        }

        Ok(nodes)
    }
}

/// A server Node with retained client ownership and a path from RootFolder.
///
/// Obtain Nodes through ``client.get_node()``, the standard folder properties,
/// ``get_child()``, or ``children``. Named attribute getters perform server reads
/// and return ``AttributeValue[T]``; ``node_id`` and ``browse_path`` are local.
#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub(crate) struct Node {
    client: OpcUaClient,
    node_id: ua::NodeId,
    browse_path: ua::BrowsePath,
}

impl Node {
    pub(crate) fn root(client: OpcUaClient) -> Self {
        Self {
            client,
            node_id: ua::ObjectId::RootFolder.into(),
            browse_path: ua::BrowsePath {
                starting_node: ua::ObjectId::RootFolder.into(),
                relative_path: ua::RelativePath::default(),
            },
        }
    }

    fn well_known(client: OpcUaClient, id: ua::ObjectId, name: &str) -> Self {
        let mut node = Self::root(client);
        node.node_id = id.into();
        node.browse_path.relative_path =
            ua::RelativePath::from([ua::QualifiedName::new(0, name)].as_slice());

        node
    }

    pub(crate) fn client(&self) -> &OpcUaClient {
        &self.client
    }

    pub(crate) fn node_id(&self) -> &ua::NodeId {
        &self.node_id
    }

    pub fn browse_path(&self) -> &ua::BrowsePath {
        &self.browse_path
    }

    async fn read_native(&self, attribute: ua::AttributeId) -> PyResult<ua::DataValue> {
        let node_id = self.node_id().clone();

        self.client
            .with_session(async move |session| {
                let request = ua::ReadValueId::new(node_id, attribute);

                let values = session
                    .read(&[request], ua::TimestampsToReturn::Both, 0.0)
                    .await
                    .map_err(error::native)?;

                let [value] = <[ua::DataValue; 1]>::try_from(values).map_err(|values| {
                    error::status(
                        ua::StatusCode::BadUnexpectedError,
                        format!(
                            "attribute read returned {} results; expected one",
                            values.len()
                        ),
                    )
                })?;

                Ok(value)
            })
            .await
    }

    fn read_attribute(
        &self,
        py: Python<'_>,
        attribute: ua::AttributeId,
    ) -> PyResult<PyAttributeValue> {
        let value = util::wait_cancel_safe(py, self.read_native(attribute))?;

        PyAttributeValue::from_native(py, attribute, value)
    }

    async fn collect_children(
        &self,
        max_references: u32,
        remote: &mut Vec<ua::ExpandedNodeId>,
    ) -> PyResult<Vec<Node>> {
        let description = ua::BrowseDescription {
            node_id: self.node_id.clone(),
            reference_type_id: ua::ReferenceTypeId::HierarchicalReferences.into(),
            include_subtypes: true,
            browse_direction: ua::BrowseDirection::Forward,
            node_class_mask: 0,
            result_mask: ua::BrowseResultMaskFlags::all().bits(),
        };

        let references = self
            .client
            .with_session(async |session| {
                let mut page = session.browse(&[description], max_references, None).await;
                let mut points = Vec::new();
                let mut references = Vec::new();

                let result = loop {
                    let results = match page {
                        Ok(results) => results,
                        Err(error) => break Err(error::native(error)),
                    };

                    let count = results.len();
                    let status = results.first().map(|result| result.status_code);
                    if count == 1 && status.is_some_and(|status| status.is_good()) {
                        points.clear();
                    }

                    for result in results {
                        if !result.continuation_point.is_null_or_empty() {
                            points.push(result.continuation_point);
                        }

                        for reference in result.references.into_iter().flatten() {
                            if reference.node_id.server_index == 0 {
                                references.push(reference);
                            } else {
                                remote.push(reference.node_id);
                            }
                        }
                    }

                    if count != 1 {
                        break Err(error::status(
                            ua::StatusCode::BadUnexpectedError,
                            format!("expected exactly one browse result; found {count}"),
                        ));
                    }

                    if let Some(status) = status.filter(|status| !status.is_good()) {
                        break Err(error::status(status, "browse failed"));
                    }

                    if points.is_empty() {
                        break Ok(references);
                    }

                    page = session.browse_next(false, &points).await;
                };

                if result.is_err() && !points.is_empty() {
                    let _ = session.browse_next(true, &points).await;
                }

                result
            })
            .await?;

        Ok(references
            .into_iter()
            .map(|reference| {
                let mut browse_path = self.browse_path.clone();
                let element = ua::RelativePathElement {
                    reference_type_id: ua::ReferenceTypeId::HierarchicalReferences.into(),
                    include_subtypes: true,
                    is_inverse: false,
                    target_name: reference.browse_name,
                };

                browse_path
                    .relative_path
                    .elements
                    .get_or_insert_default()
                    .push(element);

                Node {
                    client: self.client.clone(),
                    browse_path,
                    node_id: reference.node_id.node_id,
                }
            })
            .collect())
    }
}

#[pymethods]
impl Node {
    /// NodeId: The locally retained identifier; this does not query the server.
    #[getter]
    #[pyo3(name = "node_id")]
    fn node_id_py(&self) -> NodeId {
        NodeId::new(self.node_id.clone())
    }

    /// BrowsePath: The accumulated route starting at RootFolder, without a server read.
    #[getter]
    #[pyo3(name = "browse_path")]
    fn browse_path_py(&self) -> BrowsePath {
        BrowsePath::new(self.browse_path.clone())
    }

    /// Resolve one child path and retain its accumulated route from RootFolder.
    ///
    /// Args:
    ///     path (RelativePath | str): Nonempty path relative to this Node.
    ///
    /// Returns:
    ///     Node: The single fully resolved local target.
    #[pyo3(name = "get_child")]
    pub fn get_child_py(&self, py: Python<'_>, path: Bound<'_, PyAny>) -> PyResult<Self> {
        let path = extract_relative_path(path)?;
        util::wait_cancel_safe(py, self.get_child(path))
    }

    /// list[Node]: All immediate local hierarchical children, across every page.
    ///
    /// Out-of-server references are omitted with one aggregated ``RuntimeWarning``.
    /// Enumeration failure returns no partial list and retains the original error.
    /// If Python interrupts the wait, browsing continues in the background.
    #[getter]
    fn children(&self, py: Python<'_>) -> PyResult<Vec<Self>> {
        let node = self.clone();

        let (result, remote) = util::wait_detached(py, async move {
            let mut remote = Vec::new();
            let result = node.collect_children(0, &mut remote).await;
            Ok((result, remote))
        })?;

        if !remote.is_empty() {
            let message = CString::new(format!(
                "omitted out-of-server child references: {remote:?}"
            ))
            .map_err(|error| PyValueError::new_err(error.to_string()))?;

            if let Err(warning) = PyErr::warn(py, &py.get_type::<PyRuntimeWarning>(), &message, 1) {
                if let Err(error) = result {
                    error.set_context(py, Some(warning));
                    return Err(error);
                }

                return Err(warning);
            }
        }

        result
    }

    /// Read an attribute with its semantic payload and original quality metadata.
    ///
    /// Args:
    ///     attr (AttributeId | int | str): Attribute ID, or case-insensitive
    ///         canonical or snake-case attribute name.
    ///
    /// Returns:
    ///     AttributeValue: Result whose payload is already converted. Access
    ///         ``.value`` to retrieve it. A failed conversion raises then.
    ///         Bad or Uncertain sample quality alone does not raise.
    fn get_attribute(&self, py: Python<'_>, attr: &Bound<'_, PyAny>) -> PyResult<PyAttributeValue> {
        let attribute = attribute_id(attr)?;
        self.read_attribute(py, attribute)
    }

    fn __repr__(&self) -> String {
        format!(
            "Node('{}')",
            String::from(&self.browse_path().relative_path)
        )
    }
}

impl Node {
    async fn get_child(&self, relative_path: ua::RelativePath) -> PyResult<Node> {
        let browse_path = translation_request(self.node_id.clone(), relative_path.clone())?;
        let results = self
            .client
            .with_session(async |session| {
                session
                    .translate_browse_paths_to_node_ids(&[browse_path])
                    .await
                    .map_err(error::native)
            })
            .await?;

        let [result] = <[ua::BrowsePathResult; 1]>::try_from(results)
            .map_err(|results| translation_count_error(results.len(), 1))?;
        let node_id = translated_node_id(result)?;

        let elements = self
            .browse_path
            .relative_path
            .elements
            .as_deref()
            .unwrap_or(&[])
            .iter()
            .cloned()
            .chain(relative_path.elements.unwrap_or_default())
            .collect::<Vec<_>>();

        let browse_path = ua::BrowsePath {
            starting_node: ua::ObjectId::RootFolder.into(),
            relative_path: ua::RelativePath {
                elements: Some(elements),
            },
        };

        Ok(Node {
            client: self.client.clone(),
            node_id,
            browse_path,
        })
    }
}

#[pymethods]
impl OpcUaClient {
    /// Resolve one Node from RootFolder.
    ///
    /// Args:
    ///     path (RelativePath | str): Nonempty path starting at RootFolder.
    ///
    /// Returns:
    ///     Node: The single fully resolved local Node.
    ///
    /// Raises:
    ///     OpcUaError: Translation fails, is ambiguous, or leaves unresolved or
    ///         out-of-server targets.
    fn get_node(&self, py: Python<'_>, path: Bound<'_, PyAny>) -> PyResult<Node> {
        util::wait_cancel_safe(
            py,
            Node::root(self.clone()).get_child(extract_relative_path(path)?),
        )
    }

    /// Node: The standard ObjectsFolder, without a server read.
    #[getter]
    fn objects(&self) -> Node {
        Node::well_known(self.clone(), ua::ObjectId::ObjectsFolder, "Objects")
    }

    /// Node: The standard TypesFolder, without a server read.
    #[getter]
    fn types(&self) -> Node {
        Node::well_known(self.clone(), ua::ObjectId::TypesFolder, "Types")
    }

    /// Node: The standard ViewsFolder, without a server read.
    #[getter]
    fn views(&self) -> Node {
        Node::well_known(self.clone(), ua::ObjectId::ViewsFolder, "Views")
    }

    /// Read the value of any number of nodes. Returned values are indexed by the input order.
    #[pyo3(signature = (
        nodes: "Iterable[Node | RelativePath | str]",
        attr: "AttributeId | int | str | None" = None,
    ) -> "list[AttributeValue[AttributePayload]]")]
    pub fn read_nodes<'py>(
        &self,
        py: Python<'py>,
        nodes: &Bound<'py, PyAny>,
        attr: Option<&Bound<'py, PyAny>>,
    ) -> PyResult<Vec<PyAttributeValue>> {
        let ua_attr = attr
            .map(attribute_id)
            .transpose()?
            .unwrap_or(ua::AttributeId::Value);

        let inputs = NodeInput::materialize(self, nodes)?;
        if inputs.is_empty() {
            return Ok(Vec::new());
        }

        let n_nodes = inputs.len();
        let paths = inputs
            .iter()
            .filter_map(|input| match input {
                NodeInput::Node(_) => None,
                NodeInput::Path(path) => Some(path.clone()),
            })
            .collect();

        let results = util::wait_cancel_safe(py, async {
            let mut translated = self.translate(paths).await?.into_iter();
            let mut requests = Vec::with_capacity(n_nodes);
            for input in inputs {
                let node = match input {
                    NodeInput::Node(node) => node,
                    NodeInput::Path(_) => translated.next().ok_or_else(|| {
                        error::status(
                            ua::StatusCode::BadUnexpectedError,
                            "missing translated Node",
                        )
                    })?,
                };
                requests.push(ua::ReadValueId::new(node.node_id, ua_attr));
            }

            self.with_session(async move |session| {
                session
                    .read(&requests, ua::TimestampsToReturn::Both, 0.)
                    .await
                    .map_err(error::native)
            })
            .await
        })?;

        if results.len() != n_nodes {
            return Err(error::status(
                ua::StatusCode::BadUnexpectedError,
                format!(
                    "read returned {} results; expected {}",
                    results.len(),
                    n_nodes
                ),
            ));
        }

        let values = results
            .into_iter()
            .map(|result| PyAttributeValue::from_native(py, ua_attr, result))
            .collect::<PyResult<_>>()?;

        Ok(values)
    }

    /// Resolve paths from RootFolder in one request and return Nodes in input order.
    #[pyo3(name = "translate")]
    pub fn translate_py(&self, py: Python<'_>, paths: &Bound<'_, PyAny>) -> PyResult<Vec<Node>> {
        let paths = paths
            .try_iter()?
            .map(|item| extract_relative_path(item?))
            .collect::<PyResult<Vec<_>>>()?;
        util::wait_cancel_safe(py, self.translate(paths))
    }
}

impl OpcUaClient {
    pub(crate) async fn translate(&self, paths: Vec<ua::RelativePath>) -> PyResult<Vec<Node>> {
        if paths.is_empty() {
            return Ok(Vec::new());
        }

        let paths = paths
            .into_iter()
            .map(|path| translation_request(ua::ObjectId::RootFolder.into(), path))
            .collect::<PyResult<Vec<_>>>()?;

        let results = self
            .with_session(async |session| {
                session
                    .translate_browse_paths_to_node_ids(&paths)
                    .await
                    .map_err(error::native)
            })
            .await?;

        if results.len() != paths.len() {
            return Err(translation_count_error(results.len(), paths.len()));
        }

        results
            .into_iter()
            .zip(paths)
            .map(|(result, browse_path)| {
                Ok(Node {
                    client: self.clone(),
                    node_id: translated_node_id(result)?,
                    browse_path,
                })
            })
            .collect()
    }
}

macro_rules! attribute_getters {
    ($($attribute:ident),* $(,)?) => {
        paste::paste! {
            #[pymethods]
            impl Node {
                $(
                    #[getter]
                    fn [<$attribute:snake>](&self, py: Python<'_>) -> PyResult<PyAttributeValue> {
                        self.read_attribute(py, ua::AttributeId::$attribute)
                    }
                )*
            }
        }
    };
}

attribute_getters! {
    NodeClass,
    BrowseName,
    DisplayName,
    Description,
    WriteMask,
    UserWriteMask,
    IsAbstract,
    Symmetric,
    InverseName,
    ContainsNoLoops,
    EventNotifier,
    Value,
    DataType,
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

pub(crate) fn extract_relative_path(path: Bound<'_, PyAny>) -> PyResult<ua::RelativePath> {
    let path = if let Ok(path) = path.cast_exact::<RelativePath>() {
        path.get().as_ua_ref().clone()
    } else if let Ok(path) = path.extract::<&str>() {
        ua::RelativePath::try_from(path)
            .map_err(|error| PyValueError::new_err(error.to_string()))?
    } else {
        return Err(PyTypeError::new_err("path must be RelativePath or str"));
    };

    if path.elements.as_ref().is_none_or(Vec::is_empty) {
        return Err(PyValueError::new_err(
            "relative path must contain a path element",
        ));
    }

    Ok(path)
}

pub(crate) fn attribute_id(value: &Bound<'_, PyAny>) -> PyResult<ua::AttributeId> {
    if let Ok(value) = value.cast_exact::<AttributeId>() {
        return Ok(*value.borrow().as_ua_ref());
    }

    if let Ok(name) = value.extract::<String>() {
        return attributes::named_attribute(&name)
            .ok_or_else(|| PyValueError::new_err(format!("invalid attribute id: {name:?}")));
    }

    let NativeInteger(id) = value.extract::<NativeInteger<u32>>()?;
    ua::AttributeId::from_u32(id).map_err(|error| PyValueError::new_err(error.to_string()))
}

fn translation_request(
    starting_node: ua::NodeId,
    relative_path: ua::RelativePath,
) -> PyResult<ua::BrowsePath> {
    if relative_path.elements.as_ref().is_none_or(Vec::is_empty) {
        return Err(PyValueError::new_err(
            "relative path must contain a path element",
        ));
    }

    Ok(ua::BrowsePath {
        starting_node,
        relative_path,
    })
}

fn translation_count_error(received: usize, expected: usize) -> PyErr {
    error::status(
        ua::StatusCode::BadUnexpectedError,
        format!("path translation returned {received} results; expected {expected}"),
    )
}

fn translated_node_id(result: ua::BrowsePathResult) -> PyResult<ua::NodeId> {
    if !result.status_code.is_good() {
        return Err(error::status(
            result.status_code,
            "browse path translation failed",
        ));
    }

    let mut targets = result.targets.unwrap_or_default();
    let incomplete_or_remote = targets.iter().any(|target| {
        target.remaining_path_index != u32::MAX || target.target_id.server_index != 0
    });

    if incomplete_or_remote {
        return Err(error::status(
            ua::StatusCode::UncertainReferenceOutOfServer,
            "browse path has unresolved or out-of-server targets",
        ));
    }

    if targets.len() > 1 {
        return Err(error::status(
            ua::StatusCode::BadTooManyMatches,
            "browse path resolved to multiple targets",
        ));
    }

    targets
        .pop()
        .map(|target| target.target_id.node_id)
        .ok_or_else(|| error::status(ua::StatusCode::BadNoMatch, "browse path has no targets"))
}

#[cfg(test)]
#[allow(clippy::expect_used)]
mod browsing_tests {
    use instro_opcua_test::FolderSpec;
    use instro_opcua_test::ParentRef;
    use instro_opcua_test::TestServer;
    use ua::StatusCode as Code;

    use super::*;
    use crate::client::lifecycle_tests::failure_status;
    use crate::client::lifecycle_tests::native_client;

    fn target(id: u32) -> ua::BrowsePathTarget {
        ua::BrowsePathTarget {
            target_id: ua::NodeId::new(2, id).into(),
            remaining_path_index: u32::MAX,
        }
    }

    #[test]
    fn translation_checks_status_ambiguity_and_remote_resolution() {
        Python::initialize();

        let mut remote = target(41);
        remote.target_id.server_index = 1;
        let mut unresolved = target(42);
        unresolved.remaining_path_index = 0;
        let cases = [
            (Code::BadTimeout, None, Code::BadTimeout),
            (Code::Good, None, Code::BadNoMatch),
            (
                Code::Good,
                Some(vec![target(41), target(42)]),
                Code::BadTooManyMatches,
            ),
            (
                Code::Good,
                Some(vec![remote]),
                Code::UncertainReferenceOutOfServer,
            ),
            (
                Code::Good,
                Some(vec![unresolved]),
                Code::UncertainReferenceOutOfServer,
            ),
        ];

        for (status_code, targets, expected) in cases {
            let result = ua::BrowsePathResult {
                status_code,
                targets,
            };

            assert_eq!(failure_status(translated_node_id(result)), expected);
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn native_page_limit_preserves_all_children_and_paths_from_root() -> PyResult<()> {
        let server = TestServer::builder()
            .add_folder(FolderSpec::new(7100_u32, "Panel"))
            .add_folder(FolderSpec::new(7101_u32, "Left").parent(ParentRef::Label("Panel".into())))
            .add_folder(FolderSpec::new(7102_u32, "Right").parent(ParentRef::Label("Panel".into())))
            .add_folder(
                FolderSpec::new(7103_u32, "Center").parent(ParentRef::Label("Panel".into())),
            )
            .start()
            .expect("server starts");

        let pki = tempfile::tempdir().expect("temporary PKI");
        let client = native_client(&server, pki.path()).await;
        let connected = client
            .with_session(async |session| Ok(session.wait_for_connection().await))
            .await?;

        assert!(connected);

        let namespace = server.namespace_index();

        let path_str = format!("/Objects/{namespace}:Panel");
        let path = ua::RelativePath::try_from(&path_str).expect("panel path");

        let node = Node::root(client.clone())
            .get_child(path)
            .await
            .expect("panel");
        let children = node
            .collect_children(1, &mut Vec::new())
            .await
            .expect("browse task");

        let mut actual = children
            .into_iter()
            .map(|node| {
                (
                    node.node_id,
                    node.browse_path.starting_node,
                    String::from(&node.browse_path.relative_path),
                )
            })
            .collect::<Vec<_>>();

        actual.sort_by_key(|(id, _, _)| id.to_string());
        let expected = [(7101, "Left"), (7102, "Right"), (7103, "Center")].map(|(id, name)| {
            (
                ua::NodeId::new(namespace, id),
                ua::NodeId::new(0, 84_u32),
                format!("/0:Objects/{namespace}:Panel/{namespace}:{name}"),
            )
        });

        assert_eq!(actual, expected);
        client.disconnect_async().await?;
        Ok(())
    }
}
