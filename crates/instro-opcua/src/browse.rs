//! Immutable OPC UA browse graphs and reusable local queries.

use std::collections::HashMap;
use std::collections::HashSet;
use std::fmt;
use std::sync::Arc;

use anyhow::Context as _;
use anyhow::Result;
use anyhow::bail;
use globset::GlobBuilder;
use globset::GlobMatcher;
use open62541::ua;

use crate::client::OpcUaClient;
use crate::path::OpcUaBrowseName;
use crate::path::OpcUaBrowsePath;
use crate::path::split_absolute_segments;
use crate::path::split_namespace_prefix;
use crate::types::OpcUaNode;
use crate::types::OpcUaNodeClass;
use crate::types::OpcUaNodeId;
use crate::types::OpcUaNodeReadTarget;

const ROOT_NODE_ID: OpcUaNodeId = OpcUaNodeId::numeric(0, 84);

/// Options shared by recursive browse operations.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
#[non_exhaustive]
pub struct OpcUaBrowseOptions {
    /// Maximum number of edges below each selected start route.
    pub max_depth: Option<usize>,
}

impl OpcUaBrowseOptions {
    /// Creates options with no depth limit.
    pub const fn new() -> Self {
        Self { max_depth: None }
    }

    /// Limits the number of edges collected below each start route.
    #[must_use]
    pub const fn with_max_depth(mut self, max_depth: usize) -> Self {
        self.max_depth = Some(max_depth);
        self
    }
}

/// Describes whether a route's outgoing references were expanded.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum OpcUaExpansion {
    /// The complete ordered child snapshot was collected.
    Expanded,
    /// Expansion stopped at the caller-selected maximum depth.
    DepthLimited,
    /// The route repeated a node already in its own ancestry.
    Cycle,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct RouteIndex(usize);

#[derive(Debug, Clone)]
struct RouteNode {
    node: OpcUaNode,
    parent: Option<RouteIndex>,
    children: Vec<RouteIndex>,
    expansion: OpcUaExpansion,
}

#[derive(Debug, Default)]
struct NodeGraph {
    routes: Vec<RouteNode>,
    root_path: OpcUaBrowsePath,
}

impl NodeGraph {
    fn route(&self, index: RouteIndex) -> &RouteNode {
        let Some(route) = self.routes.get(index.0) else {
            unreachable!("route indices are created by their backing graph");
        };
        route
    }

    fn route_mut(&mut self, index: RouteIndex) -> &mut RouteNode {
        let Some(route) = self.routes.get_mut(index.0) else {
            unreachable!("route indices are created by their backing graph");
        };
        route
    }

    fn set_expansion(&mut self, index: RouteIndex, expansion: OpcUaExpansion) {
        self.route_mut(index).expansion = expansion;
    }

    fn add_root(&mut self, node: OpcUaNode) -> RouteIndex {
        let index = RouteIndex(self.routes.len());

        self.routes.push(RouteNode {
            node,
            parent: None,
            children: Vec::new(),
            expansion: OpcUaExpansion::DepthLimited,
        });

        index
    }

    fn add_child(&mut self, parent: RouteIndex, node: OpcUaNode) -> RouteIndex {
        let index = RouteIndex(self.routes.len());

        self.routes.push(RouteNode {
            node,
            parent: Some(parent),
            children: Vec::new(),
            expansion: OpcUaExpansion::DepthLimited,
        });

        self.route_mut(parent).children.push(index);

        index
    }

    fn path_segments(&self, index: RouteIndex) -> Vec<&OpcUaBrowseName> {
        let mut current = index;
        let mut suffix = Vec::new();

        loop {
            let route = self.route(current);
            if let Some(parent) = route.parent {
                suffix.push(route.node.browse_name());
                current = parent;
                continue;
            }

            let mut segments = self.root_path.segments().iter().collect::<Vec<_>>();
            segments.extend(suffix.into_iter().rev());
            return segments;
        }
    }

    fn browse_path(&self, index: RouteIndex) -> OpcUaBrowsePath {
        self.path_segments(index).into_iter().cloned().collect()
    }

    fn path_matches(&self, index: RouteIndex, path: &OpcUaBrowsePath) -> bool {
        let segments = self.path_segments(index);
        segments.len() == path.segments().len()
            && segments
                .into_iter()
                .zip(path.segments())
                .all(|(actual, expected)| actual == expected)
    }
}

/// Immutable route topology collected by one browse operation.
#[derive(Clone, Default)]
pub struct OpcUaNodeGraph {
    inner: Arc<NodeGraph>,
}

impl fmt::Debug for OpcUaNodeGraph {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OpcUaNodeGraph")
            .field(
                "roots",
                &self
                    .inner
                    .routes
                    .iter()
                    .filter(|route| route.parent.is_none())
                    .count(),
            )
            .field("routes", &self.inner.routes.len())
            .finish()
    }
}

impl OpcUaNodeGraph {
    fn from_graph(graph: NodeGraph) -> Self {
        Self {
            inner: Arc::new(graph),
        }
    }

    fn route(&self, index: RouteIndex) -> OpcUaRoute {
        OpcUaRoute {
            graph: self.clone(),
            index,
        }
    }

    /// Returns every route in server browse order.
    pub fn routes(&self) -> impl DoubleEndedIterator<Item = OpcUaRoute> + ExactSizeIterator + '_ {
        (0..self.len()).map(|index| self.route(RouteIndex(index)))
    }

    /// Returns every selected start route.
    pub fn roots(&self) -> OpcUaQuery {
        OpcUaQuery::new(
            self.clone(),
            self.inner
                .routes
                .iter()
                .enumerate()
                .filter_map(|(index, route)| route.parent.is_none().then_some(RouteIndex(index)))
                .collect(),
        )
    }

    /// Returns a reusable query over every route.
    pub fn query(&self) -> OpcUaQuery {
        OpcUaQuery::new(
            self.clone(),
            (0..self.len()).map(RouteIndex).collect::<Vec<_>>(),
        )
    }

    /// Returns every route whose absolute path equals `path`.
    pub fn resolve_path(&self, path: &OpcUaBrowsePath) -> OpcUaQuery {
        self.query().resolve_path(path)
    }

    /// Matches routes against an absolute filesystem-style glob.
    pub fn find_all(&self, pattern: impl AsRef<str>) -> Result<OpcUaQuery> {
        self.query().find_all(pattern)
    }

    /// Returns read targets for every route.
    pub fn read_targets(&self) -> impl Iterator<Item = OpcUaNodeReadTarget> + '_ {
        self.routes().map(|route| route.to_read_target())
    }

    /// Returns the number of routes.
    pub fn len(&self) -> usize {
        self.inner.routes.len()
    }

    /// Returns whether the graph contains no routes.
    pub fn is_empty(&self) -> bool {
        self.inner.routes.is_empty()
    }
}

/// A reusable route selection tied to one immutable graph.
#[derive(Clone)]
pub struct OpcUaQuery {
    graph: OpcUaNodeGraph,
    indices: Vec<RouteIndex>,
}

impl fmt::Debug for OpcUaQuery {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OpcUaQuery")
            .field("routes", &self.indices.len())
            .field("graph", &self.graph)
            .finish()
    }
}

impl OpcUaQuery {
    fn new(graph: OpcUaNodeGraph, indices: Vec<RouteIndex>) -> Self {
        Self { graph, indices }
    }

    fn select(&self, mut predicate: impl FnMut(RouteIndex, &RouteNode) -> bool) -> Self {
        let indices = self
            .indices
            .iter()
            .copied()
            .filter(|index| predicate(*index, self.graph.inner.route(*index)))
            .collect();

        Self::new(self.graph.clone(), indices)
    }

    /// Returns the immutable graph backing this query.
    pub const fn graph(&self) -> &OpcUaNodeGraph {
        &self.graph
    }

    /// Iterates over the selected routes without consuming the query.
    pub fn routes(&self) -> impl DoubleEndedIterator<Item = OpcUaRoute> + ExactSizeIterator + '_ {
        self.indices
            .iter()
            .copied()
            .map(|index| self.graph.route(index))
    }

    /// Returns selected routes whose absolute path equals `path`.
    pub fn resolve_path(&self, path: &OpcUaBrowsePath) -> Self {
        self.select(|index, _| self.graph.inner.path_matches(index, path))
    }

    /// Matches selected routes against an absolute filesystem-style glob.
    ///
    /// A whole `**` segment matches zero or more path segments. Every other
    /// segment is passed directly to globset, including its full pattern grammar.
    /// An optional numeric `namespace:` prefix restricts one segment.
    pub fn find_all(&self, pattern: impl AsRef<str>) -> Result<Self> {
        let pattern = QueryPattern::new(pattern.as_ref())?;
        Ok(self.select(|index, _| pattern.matches(&self.graph.inner.path_segments(index))))
    }

    /// Retains Variable routes.
    pub fn variables(&self) -> Self {
        self.select(|_, route| *route.node.node_class() == OpcUaNodeClass::Variable)
    }

    /// Retains fully expanded routes that have no children.
    pub fn leaves(&self) -> Self {
        self.select(|_, route| {
            route.expansion == OpcUaExpansion::Expanded && route.children.is_empty()
        })
    }

    /// Returns read targets for the selected routes.
    pub fn read_targets(&self) -> impl Iterator<Item = OpcUaNodeReadTarget> + '_ {
        self.routes().map(|route| route.to_read_target())
    }

    /// Collects selected read targets into a vector.
    pub fn to_read_targets(&self) -> Vec<OpcUaNodeReadTarget> {
        self.read_targets().collect()
    }

    /// Returns the number of selected routes.
    pub fn len(&self) -> usize {
        self.indices.len()
    }

    /// Returns whether no routes are selected.
    pub fn is_empty(&self) -> bool {
        self.indices.is_empty()
    }
}

/// An owned handle to one route in an immutable graph.
#[derive(Clone)]
pub struct OpcUaRoute {
    graph: OpcUaNodeGraph,
    index: RouteIndex,
}

impl fmt::Debug for OpcUaRoute {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OpcUaRoute")
            .field("node", self.node())
            .field("path", &self.browse_path())
            .field("expansion", &self.expansion())
            .finish()
    }
}

impl OpcUaRoute {
    fn route(&self) -> &RouteNode {
        self.graph.inner.route(self.index)
    }

    /// Returns the node metadata at this route.
    pub fn node(&self) -> &OpcUaNode {
        &self.route().node
    }

    /// Returns the route's rooted absolute path.
    pub fn browse_path(&self) -> OpcUaBrowsePath {
        self.graph.inner.browse_path(self.index)
    }

    /// Returns the route's expansion outcome.
    pub fn expansion(&self) -> OpcUaExpansion {
        self.route().expansion
    }

    /// Returns the parent route, or `None` for a selected start.
    pub fn parent(&self) -> Option<Self> {
        self.route().parent.map(|parent| self.graph.route(parent))
    }

    /// Returns children in server browse order.
    pub fn children(&self) -> OpcUaQuery {
        OpcUaQuery::new(self.graph.clone(), self.route().children.clone())
    }

    /// Converts this route into an owned read target.
    pub fn to_read_target(&self) -> OpcUaNodeReadTarget {
        OpcUaNodeReadTarget::new(self.node().node_id().clone(), self.browse_path())
    }
}

impl From<OpcUaRoute> for OpcUaNodeReadTarget {
    fn from(route: OpcUaRoute) -> Self {
        route.to_read_target()
    }
}

#[derive(Debug)]
enum QuerySegment {
    Recursive,
    Matcher {
        namespace: Option<u16>,
        matcher: GlobMatcher,
    },
}

#[derive(Debug)]
struct QueryPattern {
    segments: Vec<QuerySegment>,
}

impl QueryPattern {
    fn new(pattern: &str) -> Result<Self> {
        let segments = split_absolute_segments(pattern, '\\')?
            .into_iter()
            .map(|segment| {
                if segment == "**" {
                    Ok(QuerySegment::Recursive)
                } else {
                    let (namespace, pattern) = split_namespace_prefix(&segment, '\\')?;

                    if pattern.is_empty() {
                        bail!("OPC UA route pattern cannot contain an empty browse name");
                    }

                    let matcher = GlobBuilder::new(pattern)
                        .literal_separator(false)
                        .backslash_escape(true)
                        .build()?
                        .compile_matcher();

                    Ok(QuerySegment::Matcher { namespace, matcher })
                }
            })
            .collect::<Result<Vec<_>>>()?;

        Ok(Self { segments })
    }

    fn matches(&self, path: &[&OpcUaBrowseName]) -> bool {
        let mut reachable = vec![false; path.len() + 1];
        if let Some(first) = reachable.first_mut() {
            *first = true;
        }

        for pattern in &self.segments {
            match pattern {
                QuerySegment::Recursive => {
                    let mut matched = false;
                    for slot in &mut reachable {
                        matched |= *slot;
                        *slot = matched;
                    }
                }

                QuerySegment::Matcher { namespace, matcher } => {
                    let mut next = vec![false; reachable.len()];

                    for ((can_reach, browse_name), output) in
                        reachable.iter().zip(path).zip(next.iter_mut().skip(1))
                    {
                        *output = *can_reach
                            && namespace
                                .is_none_or(|namespace| namespace == browse_name.namespace())
                            && matcher.is_match(browse_name.name());
                    }

                    reachable = next;
                }
            }
        }

        reachable.last().copied().unwrap_or(false)
    }
}

#[allow(async_fn_in_trait)]
trait BrowseSource {
    async fn node(&self, node_id: &OpcUaNodeId) -> Result<OpcUaNode>;
    async fn children(&self, node_id: &OpcUaNodeId) -> Result<Vec<OpcUaNode>>;
}

impl BrowseSource for OpcUaClient {
    async fn node(&self, node_id: &OpcUaNodeId) -> Result<OpcUaNode> {
        let (browse_name, display_name, node_class) = self.read_node_metadata(node_id).await?;

        Ok(OpcUaNode::new(
            node_id.clone(),
            display_name,
            node_class,
            browse_name,
        ))
    }

    async fn children(&self, node_id: &OpcUaNodeId) -> Result<Vec<OpcUaNode>> {
        let description =
            ua::BrowseDescription::default().with_node_id(&ua::NodeId::from(node_id.clone()));

        let client = <OpcUaClient as std::ops::Deref>::deref(self);
        let (mut references, mut continuation) = client.browse(&description).await?;

        while let Some(point) = continuation {
            let mut results = self.browse_next(&[point]).await?;
            let result = results
                .pop()
                .context("OPC UA browse_next omitted its requested continuation result")?;
            let (next_references, next_continuation) = result?;
            references.extend(next_references);
            continuation = next_continuation;
        }

        Ok(references
            .into_iter()
            .filter_map(|reference| {
                let id = reference.node_id().node_id();
                let node_id = id
                    .clone()
                    .try_into()
                    .inspect_err(|error| {
                        tracing::warn!(
                            target: "opcua::browse",
                            ?error,
                            node_id = ?id,
                            "skipping non-local browse reference"
                        );
                    })
                    .ok()?;

                let browse_name = reference.browse_name();

                Some(OpcUaNode::new(
                    node_id,
                    reference.display_name().text().to_string(),
                    reference.node_class().into(),
                    OpcUaBrowseName::new(
                        browse_name.namespace_index(),
                        browse_name.name().to_string(),
                    ),
                ))
            })
            .collect())
    }
}

struct StartRoute {
    node: OpcUaNode,
    ancestors: HashSet<OpcUaNodeId>,
}

struct BrowseFrame {
    route: RouteIndex,
    node_id: OpcUaNodeId,
    depth: usize,
    children: Option<Arc<[OpcUaNode]>>,
    next_child: usize,
}

struct Collector<'source, S> {
    source: &'source S,
    options: OpcUaBrowseOptions,
    children: HashMap<OpcUaNodeId, Arc<[OpcUaNode]>>,
    graph: NodeGraph,
}

impl<'source, S> Collector<'source, S>
where
    S: BrowseSource + 'source,
{
    fn new(source: &'source S, options: OpcUaBrowseOptions) -> Self {
        Self {
            source,
            options,
            children: HashMap::new(),
            graph: NodeGraph::default(),
        }
    }

    async fn child_snapshot(&mut self, node_id: &OpcUaNodeId) -> Result<Arc<[OpcUaNode]>> {
        if let Some(children) = self.children.get(node_id) {
            return Ok(Arc::clone(children));
        }

        let children = Arc::from(self.source.children(node_id).await?);
        self.children.insert(node_id.clone(), Arc::clone(&children));

        Ok(children)
    }

    async fn resolve_path(&mut self, path: &OpcUaBrowsePath) -> Result<Vec<StartRoute>> {
        let root = self.source.node(&ROOT_NODE_ID).await?;

        let mut candidates = vec![StartRoute {
            node: root,
            ancestors: HashSet::from([ROOT_NODE_ID]),
        }];

        for segment in path.segments() {
            let mut matches = Vec::new();

            for candidate in candidates {
                let snapshot = self.child_snapshot(candidate.node.node_id()).await?;

                for child in snapshot.iter().filter(|node| node.browse_name() == segment) {
                    let node_id = child.node_id().clone();

                    if candidate.ancestors.contains(&node_id) {
                        continue;
                    }

                    let mut ancestors = candidate.ancestors.clone();
                    ancestors.insert(node_id);

                    matches.push(StartRoute {
                        node: child.clone(),
                        ancestors,
                    });
                }
            }

            if matches.is_empty() {
                return Ok(Vec::new());
            }

            candidates = matches;
        }

        Ok(candidates)
    }

    async fn collect(
        mut self,
        root_path: OpcUaBrowsePath,
        starts: Vec<StartRoute>,
    ) -> Result<OpcUaNodeGraph> {
        self.graph.root_path = root_path;

        for start in starts {
            let node_id = start.node.node_id().clone();
            let route = self.graph.add_root(start.node);
            let mut ancestors = start.ancestors;
            let mut stack = vec![BrowseFrame {
                route,
                node_id,
                depth: 0,
                children: None,
                next_child: 0,
            }];

            loop {
                let Some(frame) = stack.last() else {
                    break;
                };
                let should_limit = self
                    .options
                    .max_depth
                    .is_some_and(|max_depth| frame.depth >= max_depth);
                if should_limit {
                    let Some(frame) = stack.pop() else {
                        break;
                    };
                    self.graph
                        .set_expansion(frame.route, OpcUaExpansion::DepthLimited);
                    ancestors.remove(&frame.node_id);
                    continue;
                }

                let unloaded_node = stack
                    .last()
                    .filter(|frame| frame.children.is_none())
                    .map(|frame| frame.node_id.clone());
                if let Some(node_id) = unloaded_node {
                    let snapshot = self.child_snapshot(&node_id).await?;
                    let Some(frame) = stack.last_mut() else {
                        unreachable!("the browse frame remains present while loading children");
                    };
                    frame.children = Some(snapshot);
                    self.graph
                        .set_expansion(frame.route, OpcUaExpansion::Expanded);
                }

                let next = if let Some(frame) = stack.last_mut() {
                    let child = frame
                        .children
                        .as_ref()
                        .and_then(|children| children.get(frame.next_child))
                        .cloned();
                    frame.next_child = frame
                        .next_child
                        .saturating_add(usize::from(child.is_some()));
                    child.map(|child| (frame.route, frame.depth, child))
                } else {
                    None
                };

                let Some((parent, parent_depth, child)) = next else {
                    if let Some(frame) = stack.pop() {
                        ancestors.remove(&frame.node_id);
                    }
                    continue;
                };

                let node_id = child.node_id().clone();
                let route = self.graph.add_child(parent, child);
                if ancestors.contains(&node_id) {
                    self.graph.set_expansion(route, OpcUaExpansion::Cycle);
                    continue;
                }

                ancestors.insert(node_id.clone());
                stack.push(BrowseFrame {
                    route,
                    node_id,
                    depth: parent_depth.saturating_add(1),
                    children: None,
                    next_child: 0,
                });
            }
        }

        Ok(OpcUaNodeGraph::from_graph(self.graph))
    }
}

impl OpcUaClient {
    /// Browses the standard Root node at `/`.
    pub async fn browse_root(&self, options: OpcUaBrowseOptions) -> Result<OpcUaNodeGraph> {
        let mut collector = Collector::new(self, options);
        let root = OpcUaBrowsePath::root();
        let starts = collector.resolve_path(&root).await?;
        collector.collect(root, starts).await
    }

    /// Resolves a rooted absolute path from Root and browses every matching route.
    pub async fn browse_path(
        &self,
        path: OpcUaBrowsePath,
        options: OpcUaBrowseOptions,
    ) -> Result<OpcUaNodeGraph> {
        let mut collector = Collector::new(self, options);
        let starts = collector.resolve_path(&path).await?;
        collector.collect(path, starts).await
    }

    /// Browses `node_id` mounted at the asserted rooted absolute `path`.
    ///
    /// The supplied path must end in the node's actual browse name. The standard
    /// Root node is mounted only at `/`.
    pub async fn browse_from(
        &self,
        node_id: OpcUaNodeId,
        path: OpcUaBrowsePath,
        options: OpcUaBrowseOptions,
    ) -> Result<OpcUaNodeGraph> {
        let collector = Collector::new(self, options);
        let node = collector.source.node(&node_id).await?;
        validate_mount(&node, &path)?;
        collector
            .collect(
                path,
                vec![StartRoute {
                    node,
                    ancestors: HashSet::from([node_id]),
                }],
            )
            .await
    }
}

fn validate_mount(node: &OpcUaNode, path: &OpcUaBrowsePath) -> Result<()> {
    if node.node_id() == &ROOT_NODE_ID {
        if !path.is_root() {
            bail!("the standard Root node must be mounted at '/'");
        }
    } else if path.is_root() {
        bail!("only the standard Root node can be mounted at '/'");
    } else if path.segments().last() != Some(node.browse_name()) {
        bail!(
            "browse path {path} does not end with node browse name {}",
            node.browse_name()
        );
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;
    use std::collections::HashMap;
    use std::collections::HashSet;

    use anyhow::Context as _;
    use anyhow::Result;

    use super::BrowseSource;
    use super::Collector;
    use super::OpcUaBrowseOptions;
    use super::OpcUaExpansion;
    use super::StartRoute;
    use super::validate_mount;
    use crate::path::OpcUaBrowseName;
    use crate::types::OpcUaNode;
    use crate::types::OpcUaNodeClass;
    use crate::types::OpcUaNodeId;

    fn id(value: u32) -> OpcUaNodeId {
        OpcUaNodeId::numeric(0, value)
    }

    fn node(value: u32, name: &str, class: OpcUaNodeClass) -> OpcUaNode {
        OpcUaNode::new(
            id(value),
            name.to_owned(),
            class,
            OpcUaBrowseName::new(0, name.to_owned()),
        )
    }

    fn object(value: u32, name: &str) -> OpcUaNode {
        node(value, name, OpcUaNodeClass::Object)
    }

    fn start(node: OpcUaNode) -> StartRoute {
        let node_id = node.node_id().clone();
        StartRoute {
            node,
            ancestors: HashSet::from([node_id]),
        }
    }

    struct MockSource {
        nodes: HashMap<OpcUaNodeId, OpcUaNode>,
        children: HashMap<OpcUaNodeId, Vec<OpcUaNode>>,
        fetches: RefCell<HashMap<OpcUaNodeId, usize>>,
    }

    impl MockSource {
        fn new() -> Self {
            let root = node(84, "Root", OpcUaNodeClass::Object);
            Self {
                nodes: HashMap::from([(root.node_id().clone(), root)]),
                children: HashMap::new(),
                fetches: RefCell::new(HashMap::new()),
            }
        }

        fn add_children(&mut self, parent: u32, children: Vec<OpcUaNode>) {
            for child in &children {
                self.nodes.insert(child.node_id().clone(), child.clone());
            }
            self.children.insert(id(parent), children);
        }

        async fn browse(
            &self,
            selected: OpcUaNode,
            path: &str,
            options: OpcUaBrowseOptions,
        ) -> Result<super::OpcUaNodeGraph> {
            Collector::new(self, options)
                .collect(path.parse()?, vec![start(selected)])
                .await
        }
    }

    impl BrowseSource for MockSource {
        async fn node(&self, node_id: &OpcUaNodeId) -> Result<OpcUaNode> {
            self.nodes
                .get(node_id)
                .cloned()
                .context("mock node is missing")
        }

        async fn children(&self, node_id: &OpcUaNodeId) -> Result<Vec<OpcUaNode>> {
            *self
                .fetches
                .borrow_mut()
                .entry(node_id.clone())
                .or_default() += 1;
            Ok(self.children.get(node_id).cloned().unwrap_or_default())
        }
    }

    #[tokio::test]
    async fn every_node_class_is_expanded() -> Result<()> {
        let mut source = MockSource::new();
        source.add_children(
            1,
            vec![
                node(2, "Variable", OpcUaNodeClass::Variable),
                node(3, "Method", OpcUaNodeClass::Method),
                node(4, "View", OpcUaNodeClass::View),
                node(5, "Other", OpcUaNodeClass::Other(99)),
            ],
        );
        for value in 2..=5 {
            source.add_children(value, vec![object(value + 10, &format!("Child{value}"))]);
        }

        let graph = source
            .browse(object(1, "Start"), "/Start", OpcUaBrowseOptions::new())
            .await?;
        assert_eq!(graph.len(), 9);
        assert!(
            graph
                .routes()
                .all(|route| route.expansion() == OpcUaExpansion::Expanded)
        );
        Ok(())
    }

    #[tokio::test]
    async fn cycles_are_terminal_and_siblings_continue() -> Result<()> {
        let mut source = MockSource::new();
        source.add_children(1, vec![object(2, "Loop"), object(3, "Sibling")]);
        source.add_children(2, vec![object(1, "Start")]);
        source.add_children(3, vec![object(4, "Leaf")]);

        let graph = source
            .browse(object(1, "Start"), "/Start", OpcUaBrowseOptions::new())
            .await?;
        let cycle = graph
            .resolve_path(&"/Start/Loop/Start".parse()?)
            .routes()
            .next();
        let sibling = graph
            .resolve_path(&"/Start/Sibling/Leaf".parse()?)
            .routes()
            .next();
        assert_eq!(
            cycle.map(|route| route.expansion()),
            Some(OpcUaExpansion::Cycle)
        );
        assert!(sibling.is_some());
        Ok(())
    }

    #[tokio::test]
    async fn depth_limited_routes_are_not_leaves() -> Result<()> {
        let mut source = MockSource::new();
        source.add_children(1, vec![object(2, "Child")]);
        source.add_children(2, vec![object(3, "Grandchild")]);

        let graph = source
            .browse(
                object(1, "Start"),
                "/Start",
                OpcUaBrowseOptions::new().with_max_depth(1),
            )
            .await?;
        let child = graph.resolve_path(&"/Start/Child".parse()?);
        assert_eq!(
            child.routes().next().map(|route| route.expansion()),
            Some(OpcUaExpansion::DepthLimited)
        );
        assert!(child.leaves().is_empty());
        Ok(())
    }

    #[tokio::test]
    async fn duplicate_routes_share_one_child_snapshot() -> Result<()> {
        let mut source = MockSource::new();
        source.add_children(1, vec![object(2, "Left"), object(3, "Right")]);
        source.add_children(2, vec![object(4, "Shared")]);
        source.add_children(3, vec![object(4, "Shared")]);
        source.add_children(4, vec![object(5, "Leaf")]);

        let graph = source
            .browse(object(1, "Start"), "/Start", OpcUaBrowseOptions::new())
            .await?;
        assert_eq!(
            graph
                .resolve_path(&"/Start/Left/Shared/Leaf".parse()?)
                .len(),
            1
        );
        assert_eq!(
            graph
                .resolve_path(&"/Start/Right/Shared/Leaf".parse()?)
                .len(),
            1
        );
        assert_eq!(source.fetches.borrow().get(&id(4)), Some(&1));
        Ok(())
    }

    #[tokio::test]
    async fn absolute_path_resolution_keeps_every_match_and_reuses_cache() -> Result<()> {
        let mut source = MockSource::new();
        source.add_children(84, vec![object(1, "Device"), object(2, "Device")]);
        source.add_children(1, vec![object(3, "LeafA")]);
        source.add_children(2, vec![object(4, "LeafB")]);

        let mut collector = Collector::new(&source, OpcUaBrowseOptions::new());
        let path = "/Device".parse()?;
        let starts = collector.resolve_path(&path).await?;
        let graph = collector.collect(path, starts).await?;
        assert_eq!(graph.roots().len(), 2);
        assert_eq!(source.fetches.borrow().get(&id(84)), Some(&1));
        assert!(graph.resolve_path(&"/Device/LeafA".parse()?).len() == 1);
        assert!(graph.resolve_path(&"/Device/LeafB".parse()?).len() == 1);
        Ok(())
    }

    #[test]
    fn explicit_mounts_validate_root_and_final_browse_name() -> Result<()> {
        let root = node(84, "Root", OpcUaNodeClass::Object);
        let sensor = object(1, "Sensor");
        assert!(validate_mount(&root, &"/".parse()?).is_ok());
        assert!(validate_mount(&root, &"/Root".parse()?).is_err());
        assert!(validate_mount(&sensor, &"/Objects/Sensor".parse()?).is_ok());
        assert!(validate_mount(&sensor, &"/".parse()?).is_err());
        assert!(validate_mount(&sensor, &"/Objects/Other".parse()?).is_err());
        Ok(())
    }

    #[tokio::test]
    async fn reusable_single_graph_queries_preserve_globset_grammar() -> Result<()> {
        let mut source = MockSource::new();
        source.add_children(
            1,
            vec![
                node(2, "Temperature", OpcUaNodeClass::Variable),
                node(3, "Pressure", OpcUaNodeClass::Variable),
                object(4, "Group"),
                node(6, "2:A/B", OpcUaNodeClass::Variable),
            ],
        );
        source.add_children(4, vec![node(5, "Flow1", OpcUaNodeClass::Variable)]);

        let graph = source
            .browse(object(1, "Start"), "/Start", OpcUaBrowseOptions::new())
            .await?;
        let query = graph.find_all("/Start/**/{Temperature,Pressure,Flow[0-9]}")?;
        assert_eq!(query.variables().leaves().len(), 3);
        assert_eq!(query.routes().count(), 3);
        assert_eq!(query.routes().count(), 3);
        assert_eq!(graph.find_all(r"/Start/2\:A\/B")?.len(), 1);
        assert!(graph.find_all("Start/**").is_err());
        Ok(())
    }
}
