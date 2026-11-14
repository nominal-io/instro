use std::ffi::CString;

use futures_util::future::BoxFuture;
use opcua::types as ua;
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyRuntimeWarning;
use pyo3::exceptions::PyTypeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyInt;

use crate::client::OpcUaClient;
use crate::error;
use crate::util;
use crate::values::BrowsePath;
use crate::values::NodeId;
use crate::values::PyAttributeValue;
use crate::values::RelativePath;
use crate::values::UaType;
use crate::values::attributes::Attribute;
use crate::values::attributes::AttributeValue;
use crate::values::attributes::{self};

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
                nodes.push(Self::Path(relative_path(&item)?));
            }
        }

        if nodes.is_empty() {
            return Err(PyValueError::new_err(
                "subscription requires at least one Node",
            ));
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

    pub(crate) fn id(&self) -> &ua::NodeId {
        &self.node_id
    }

    async fn resolve(&self, path: ua::RelativePath) -> PyResult<Self> {
        let node = self.clone();
        self.client
            .with_session(async move |session| node.resolve_in(session, path).await)
            .await
    }

    pub(crate) async fn resolve_in(
        &self,
        session: &opcua::client::Session,
        path: ua::RelativePath,
    ) -> PyResult<Self> {
        if path.elements.as_ref().is_none_or(Vec::is_empty) {
            return Err(PyValueError::new_err(
                "relative path must contain a path element",
            ));
        }

        let request = ua::BrowsePath {
            starting_node: self.node_id.clone(),
            relative_path: path.clone(),
        };

        let results = session
            .translate_browse_paths_to_node_ids(&[request])
            .await
            .map_err(error::native)?;

        let node_id = translated_node(results)?;

        let mut browse_path = self.browse_path.clone();
        browse_path
            .relative_path
            .elements
            .get_or_insert_default()
            .extend(path.elements.into_iter().flatten());

        Ok(Self {
            client: self.client.clone(),
            node_id,
            browse_path,
        })
    }

    async fn read_native(&self, attribute: ua::AttributeId) -> PyResult<ua::DataValue> {
        let node_id = self.node_id.clone();

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

    fn read<A: Attribute>(&self, py: Python<'_>) -> PyResult<AttributeValue<A::Value>> {
        let node = self.clone();
        util::wait(py, async move { node.read_native(A::ID).await }).map(A::wrap)
    }

    async fn enumerate_children(&self, max_references: u32) -> PyResult<ChildReferences> {
        let description = ua::BrowseDescription {
            node_id: self.node_id.clone(),
            browse_direction: ua::BrowseDirection::Forward,
            reference_type_id: ua::ReferenceTypeId::HierarchicalReferences.into(),
            include_subtypes: true,
            node_class_mask: 0,
            result_mask: ua::BrowseResultMask::All as u32,
        };

        self.client
            .with_session(async move |session| {
                let initial = session
                    .browse(&[description], max_references, None)
                    .await
                    .map_err(error::native);

                let references = collect_children(initial, |release, points| {
                    Box::pin(async move {
                        session
                            .browse_next(release, &points)
                            .await
                            .map_err(error::native)
                    })
                })
                .await;

                Ok(references)
            })
            .await
    }

    fn browse_children(&self, py: Python<'_>, max_references: u32) -> PyResult<Vec<Self>> {
        let node = self.clone();
        let references = util::wait(
            py,
            async move { node.enumerate_children(max_references).await },
        )?
        .finish(py)?;

        let children = references
            .into_iter()
            .map(|reference| {
                let mut browse_path = self.browse_path.clone();
                let element = ua::RelativePathElement {
                    reference_type_id: ua::ReferenceTypeId::HierarchicalReferences.into(),
                    is_inverse: false,
                    include_subtypes: true,
                    target_name: reference.browse_name,
                };

                browse_path
                    .relative_path
                    .elements
                    .get_or_insert_default()
                    .push(element);

                Self {
                    client: self.client.clone(),
                    node_id: reference.node_id.node_id,
                    browse_path,
                }
            })
            .collect();

        Ok(children)
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
    fn get_node(&self, py: Python<'_>, path: &Bound<'_, PyAny>) -> PyResult<Node> {
        Node::root(self.clone()).get_child(py, path)
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

        let nodes = NodeInput::materialize(self, nodes)?
            .into_iter()
            .map(|node| {
                let node = match node {
                    NodeInput::Node(node) => node,
                    NodeInput::Path(path) => {
                        self.get_node(py, &RelativePath::new(path).into_bound_py_any(py)?)?
                    }
                };

                Ok(ua::ReadValueId::new(node.node_id, ua_attr.clone()))
            })
            .collect::<PyResult<Vec<_>>>()?;

        let n_nodes = nodes.len();

        let client = self.clone();

        let results = util::wait(py, async move {
            client
                .with_session(async |session| {
                    session
                        .read(nodes.as_slice(), ua::TimestampsToReturn::Both, 0.)
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
            .map(|result| PyAttributeValue::from_native(ua_attr.clone(), result))
            .collect();

        Ok(values)
    }
}

#[pymethods]
impl Node {
    /// NodeId: The locally retained identifier; this does not query the server.
    #[getter]
    fn node_id(&self) -> NodeId {
        NodeId::new(self.node_id.clone())
    }

    /// BrowsePath: The accumulated route starting at RootFolder, without a server read.
    #[getter]
    fn browse_path(&self) -> BrowsePath {
        BrowsePath::new(self.browse_path.clone())
    }

    /// Resolve one child path and retain its accumulated route from RootFolder.
    ///
    /// Args:
    ///     path (RelativePath | str): Nonempty path relative to this Node.
    ///
    /// Returns:
    ///     Node: The single fully resolved local target.
    fn get_child(&self, py: Python<'_>, path: &Bound<'_, PyAny>) -> PyResult<Self> {
        let path = relative_path(path)?;

        let node = self.clone();
        util::wait(py, async move { node.resolve(path).await })
    }

    /// list[Node]: All immediate local hierarchical children, across every page.
    ///
    /// Out-of-server references are omitted with one aggregated ``RuntimeWarning``.
    /// Enumeration failure returns no partial list and retains the original error.
    #[getter]
    fn children(&self, py: Python<'_>) -> PyResult<Vec<Self>> {
        self.browse_children(py, 0)
    }

    /// Read an attribute with its semantic payload and original quality metadata.
    ///
    /// Args:
    ///     attr (AttributeId | int | str): Attribute ID, or case-insensitive
    ///         canonical or snake-case attribute name.
    ///
    /// Returns:
    ///     AttributeValue: Result with payload conversion deferred until ``.value``.
    ///         Bad or Uncertain sample quality alone does not raise.
    fn get_attribute(&self, py: Python<'_>, attr: &Bound<'_, PyAny>) -> PyResult<PyAttributeValue> {
        let attribute = attribute_id(attr)?;

        let node = self.clone();
        util::wait(py, async move {
            node.read_native(attribute)
                .await
                .map(|value| PyAttributeValue::from_native(attribute, value))
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "Node('{}')",
            String::from(&self.browse_path().as_ua_ref().relative_path)
        )
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
                        self.read::<attributes::$attribute>(py)
                            .map(attributes::$attribute::into_python)
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

pub(crate) fn relative_path(path: &Bound<'_, PyAny>) -> PyResult<ua::RelativePath> {
    if let Ok(path) = path.extract::<RelativePath>() {
        return Ok(path.as_ua_ref().clone());
    }

    if let Ok(path) = path.extract::<String>() {
        return ua::RelativePath::try_from(path)
            .map_err(|error| PyValueError::new_err(error.to_string()));
    }

    Err(PyTypeError::new_err("path must be RelativePath or str"))
}

pub(crate) fn attribute_id(value: &Bound<'_, PyAny>) -> PyResult<ua::AttributeId> {
    if let Ok(value) = value.extract::<crate::values::AttributeId>() {
        return Ok(*value.as_ua_ref());
    }

    if let Ok(name) = value.extract::<String>() {
        return attributes::named_attribute(&name)
            .ok_or_else(|| PyValueError::new_err(format!("invalid attribute id: {name:?}")));
    }

    if value.is_instance_of::<PyInt>() {
        return ua::AttributeId::from_u32(value.extract()?)
            .map_err(|error| PyValueError::new_err(error.to_string()));
    }

    Err(PyTypeError::new_err(
        "attribute must be AttributeId, int, or str",
    ))
}

fn translated_node(results: Vec<ua::BrowsePathResult>) -> PyResult<ua::NodeId> {
    let [result] = <[ua::BrowsePathResult; 1]>::try_from(results).map_err(|results| {
        error::status(
            ua::StatusCode::BadUnexpectedError,
            format!(
                "path translation returned {} results; expected one",
                results.len()
            ),
        )
    })?;

    if !result.status_code.is_good() {
        return Err(error::status(
            result.status_code,
            "browse path translation failed",
        ));
    }

    let mut targets = result.targets.unwrap_or_default();
    let has_unresolved_or_remote_target = targets.iter().any(|target| {
        target.remaining_path_index != u32::MAX || target.target_id.server_index != 0
    });

    if has_unresolved_or_remote_target {
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

struct ChildReferences {
    result: PyResult<Vec<ua::ReferenceDescription>>,
    invalid: Vec<ua::ReferenceDescription>,
}

impl ChildReferences {
    fn finish(self, py: Python<'_>) -> PyResult<Vec<ua::ReferenceDescription>> {
        let warning = if self.invalid.is_empty() {
            Ok(())
        } else {
            let message = format!("omitted out-of-server child references: {:?}", self.invalid);
            let message =
                CString::new(message).map_err(|error| PyValueError::new_err(error.to_string()))?;

            PyErr::warn(py, &py.get_type::<PyRuntimeWarning>(), &message, 1)
        };

        match (self.result, warning) {
            (Ok(_), Err(warning)) => Err(warning),
            (Ok(references), Ok(())) => Ok(references),

            (Err(operation), Err(warning)) => {
                operation.set_context(py, Some(warning));
                Err(operation)
            }

            (Err(operation), _) => Err(operation),
        }
    }
}

async fn collect_children<'a>(
    initial: PyResult<Vec<ua::BrowseResult>>,
    mut next: impl FnMut(bool, Vec<ua::ByteString>) -> BoxFuture<'a, PyResult<Vec<ua::BrowseResult>>>,
) -> ChildReferences {
    let mut results = initial;
    let mut outstanding = Vec::new();

    let mut references = Vec::new();
    let mut invalid = Vec::new();

    let result = loop {
        let page = match results {
            Ok(page) => page,
            Err(error) => break Err(error),
        };

        let count = page.len();
        let mut failure = None;
        let mut points = Vec::new();

        for result in page {
            if !result.continuation_point.is_null_or_empty() {
                points.push(result.continuation_point);
            }

            if !result.status_code.is_good() && failure.is_none() {
                failure = Some(error::status(
                    result.status_code,
                    "child enumeration failed",
                ));
            }

            for reference in result.references.into_iter().flatten() {
                if reference.node_id.server_index != 0 {
                    invalid.push(reference);
                } else {
                    references.push(reference);
                }
            }
        }

        if count != 1 {
            outstanding.extend(points);
            break Err(error::status(
                ua::StatusCode::BadUnexpectedError,
                format!("child enumeration returned {count} results; expected one"),
            ));
        }

        if let Some(error) = failure {
            outstanding.extend(points);
            break Err(error);
        }

        outstanding = points;
        if outstanding.is_empty() {
            break Ok(references);
        }

        results = next(false, outstanding.clone()).await;
    };

    if result.is_err() && !outstanding.is_empty() {
        let _ = next(true, outstanding).await;
    }

    ChildReferences { result, invalid }
}

#[cfg(test)]
mod tests;
