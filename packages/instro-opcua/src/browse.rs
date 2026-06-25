//! Recursive browsing of an OPC-UA server's address space.
//!
//! The [`Browse`] trait defines a single-level browse that returns the immediate
//! children of a given node. [`BrowseAll`] extends any `Browse` implementor with
//! a route-oriented depth-first traversal that:
//!
//! - **Detects cycles** via the current ancestor path. If a node ID repeats in
//!   its own ancestry, browsing fails.
//! - **Does not classify valid repeated topology**. Diamonds, duplicate node IDs,
//!   duplicate browse names, and duplicate browse paths are inserted as ordinary
//!   routes.
//! - **Limits depth** with an optional `max_depth` parameter.
//! - **Limits total browsed routes** with a high defensive ceiling.
//! - **Recurses into `Object`, `Variable`, and `View` nodes**. `Method` and
//!   other node classes are kept as leaves.
//!
//! The [`OpcUaClient`](super::client::OpcUaClient) implementation of `Browse`
//! handles continuation points transparently, issuing `browse_next` calls until
//! all references for a node have been collected.

use std::collections::HashSet;
use std::future::Future;
use std::pin::Pin;

use anyhow::Result;
use anyhow::bail;
use open62541::ua;

use super::client::OpcUaClient;
use super::types::OpcUaBrowseName;
use super::types::OpcUaBrowsePath;
use super::types::OpcUaNode;
use super::types::OpcUaNodeClass;
use super::types::OpcUaNodeGraph;
use super::types::OpcUaNodeId;
use super::types::OpcUaRouteId;

const MAX_BROWSED_ROUTES: usize = 1_000_000;

/// A trait for browsing a single node and returning its children.
pub trait Browse {
    /// Browse a single node and return its children.
    fn browse_node(&self, node_id: OpcUaNodeId) -> impl Future<Output = Result<Vec<OpcUaNode>>>;
}

/// A trait for browsing all routes below a root node.
pub trait BrowseAll: Browse {
    /// Browse all routes in the graph rooted at `root`.
    fn browse_all(
        &self,
        root: OpcUaNode,
        max_depth: Option<usize>,
    ) -> impl Future<Output = Result<OpcUaNodeGraph>>;
}

impl<T: Browse> BrowseAll for T {
    async fn browse_all(
        &self,
        root: OpcUaNode,
        max_depth: Option<usize>,
    ) -> Result<OpcUaNodeGraph> {
        browse_all_with_limit(self, root, max_depth, MAX_BROWSED_ROUTES).await
    }
}

impl Browse for OpcUaClient {
    async fn browse_node(&self, node_id: OpcUaNodeId) -> Result<Vec<OpcUaNode>> {
        let browse_desc = ua::BrowseDescription::default().with_node_id(&node_id.into());
        let (mut all_refs, mut cont_pt) = self.browse(&browse_desc).await?;

        while let Some(cp) = cont_pt {
            let mut results = self.browse_next(&[cp]).await?;

            match results.pop() {
                Some(result) => {
                    let (more_refs, next_cp) = result?;
                    all_refs.extend(more_refs);
                    cont_pt = next_cp;
                }
                None => break,
            }
        }

        let nodes = all_refs
            .into_iter()
            .filter_map(|reference| {
                let id = reference.node_id().node_id();

                let node_id = id
                    .clone()
                    .try_into()
                    .inspect_err(|_e| {
                        tracing::warn!(
                            target: "opcua::browse",
                            node_id = ?id,
                            "skipping reference during browse"
                        );
                    })
                    .ok()?;

                let browse_name = reference.browse_name();
                let browse_name = OpcUaBrowseName::new(
                    browse_name.namespace_index(),
                    browse_name.name().to_string(),
                );

                Some(OpcUaNode::new(
                    node_id,
                    reference.display_name().text().to_string(),
                    reference.node_class().into(),
                    browse_name,
                ))
            })
            .collect();

        Ok(nodes)
    }
}

// not taking a dep on futures just for this type
type BoxedFuture<'a, T> = Pin<Box<dyn Future<Output = Result<T>> + 'a>>;

async fn browse_all_with_limit<B: Browse>(
    browser: &B,
    root: OpcUaNode,
    max_depth: Option<usize>,
    max_routes: usize,
) -> Result<OpcUaNodeGraph> {
    let root_node_id = root.node_id().clone();
    let mut ancestors = HashSet::from([root_node_id]);
    let mut graph = OpcUaNodeGraph::new();
    let root_path = OpcUaBrowsePath::from_segment(root.browse_name().clone());
    let root_route = graph.add_root(root, root_path);

    if graph.len() > max_routes {
        bail!("OPC-UA browse route ceiling exceeded");
    }

    browse_recursive(
        browser,
        root_route,
        0,
        max_depth,
        max_routes,
        &mut ancestors,
        &mut graph,
    )
    .await?;

    Ok(graph)
}

fn browse_recursive<'a, B: Browse>(
    browser: &'a B,
    parent_route: OpcUaRouteId,
    depth: usize,
    max_depth: Option<usize>,
    max_routes: usize,
    ancestors: &'a mut HashSet<OpcUaNodeId>,
    graph: &'a mut OpcUaNodeGraph,
) -> BoxedFuture<'a, ()> {
    Box::pin(async move {
        if let Some(max_depth) = max_depth
            && depth >= max_depth
        {
            return Ok(());
        }

        let Some(parent) = graph.route(parent_route) else {
            bail!("parent OPC-UA route does not exist");
        };
        let parent_node_id = parent.node().node_id().clone();

        let raw = browser.browse_node(parent_node_id).await?;

        for node in raw {
            let node_id = node.node_id().clone();
            if ancestors.contains(&node_id) {
                bail!("cycle detected while browsing node {node_id}");
            }

            if graph.len() >= max_routes {
                bail!("OPC-UA browse route ceiling exceeded");
            }

            let should_recurse = matches!(
                node.node_class(),
                OpcUaNodeClass::Object | OpcUaNodeClass::Variable | OpcUaNodeClass::View
            );
            let child_route = graph.add_child(parent_route, node)?;

            if should_recurse {
                ancestors.insert(node_id.clone());

                browse_recursive(
                    browser,
                    child_route,
                    depth.saturating_add(1),
                    max_depth,
                    max_routes,
                    ancestors,
                    graph,
                )
                .await?;

                ancestors.remove(&node_id);
            }
        }

        Ok(())
    })
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use anyhow::Result;
    use tokio::runtime::Builder;

    use super::Browse;
    use super::BrowseAll;
    use super::browse_all_with_limit;
    use crate::types::NodeIdInner;
    use crate::types::OpcUaBrowseName;
    use crate::types::OpcUaBrowsePath;
    use crate::types::OpcUaNode;
    use crate::types::OpcUaNodeClass;
    use crate::types::OpcUaNodeGraph;
    use crate::types::OpcUaNodeId;

    fn nid(n: u32) -> OpcUaNodeId {
        OpcUaNodeId {
            namespace: 0,
            inner: NodeIdInner::Numeric(n),
        }
    }

    fn node(id: u32, class: OpcUaNodeClass) -> OpcUaNode {
        let prefix = match class {
            OpcUaNodeClass::Object => "Object",
            OpcUaNodeClass::Variable => "Variable",
            OpcUaNodeClass::Method => "Method",
            OpcUaNodeClass::View => "View",
            OpcUaNodeClass::Other(_) => "Other",
        };

        OpcUaNode::new(
            nid(id),
            format!("{prefix} {id}"),
            class,
            OpcUaBrowseName::new(0, format!("{prefix}_{id}")),
        )
    }

    fn obj(id: u32) -> OpcUaNode {
        node(id, OpcUaNodeClass::Object)
    }

    fn var(id: u32) -> OpcUaNode {
        node(id, OpcUaNodeClass::Variable)
    }

    fn method(id: u32) -> OpcUaNode {
        node(id, OpcUaNodeClass::Method)
    }

    fn view(id: u32) -> OpcUaNode {
        node(id, OpcUaNodeClass::View)
    }

    fn other(id: u32) -> OpcUaNode {
        node(id, OpcUaNodeClass::Other(10_000))
    }

    fn path(parts: &[&str]) -> OpcUaBrowsePath {
        parts
            .iter()
            .map(|part| OpcUaBrowseName::new(0, (*part).to_string()))
            .collect()
    }

    fn route_paths(graph: &OpcUaNodeGraph) -> Vec<String> {
        graph
            .routes()
            .map(|route| route.browse_path().to_string())
            .collect()
    }

    fn count_node_id(graph: &OpcUaNodeGraph, node_id: &OpcUaNodeId) -> usize {
        graph
            .routes()
            .filter(|route| route.node().node_id() == node_id)
            .count()
    }

    fn max_route_depth(graph: &OpcUaNodeGraph) -> usize {
        graph
            .routes()
            .map(|route| route.browse_path().segments().len().saturating_sub(1))
            .max()
            .unwrap_or(0)
    }

    fn child_paths(graph: &OpcUaNodeGraph, route_path: &OpcUaBrowsePath) -> Vec<String> {
        graph
            .resolve_path(route_path)
            .flat_map(|route| graph.children(route.id()))
            .map(|child| child.browse_path().to_string())
            .collect()
    }

    /// A fake `Browser` implementation backed by an adjacency map.
    struct MockBrowser {
        graph: HashMap<OpcUaNodeId, Vec<OpcUaNode>>,
    }

    impl MockBrowser {
        fn new() -> Self {
            Self {
                graph: HashMap::new(),
            }
        }

        fn add_children(&mut self, parent: OpcUaNodeId, children: Vec<OpcUaNode>) {
            self.graph.entry(parent).or_default().extend(children);
        }

        fn browse(&self, root: OpcUaNodeId, max_depth: Option<usize>) -> Result<OpcUaNodeGraph> {
            let root = OpcUaNode::new(
                root,
                "Root".to_owned(),
                OpcUaNodeClass::Object,
                OpcUaBrowseName::new(0, "Root".to_owned()),
            );

            let runtime = Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(1)
                .build()
                .expect("failed to build tokio runtime");

            runtime.block_on(self.browse_all(root, max_depth))
        }

        fn browse_with_limit(
            &self,
            root: OpcUaNodeId,
            max_routes: usize,
        ) -> Result<OpcUaNodeGraph> {
            let root = OpcUaNode::new(
                root,
                "Root".to_owned(),
                OpcUaNodeClass::Object,
                OpcUaBrowseName::new(0, "Root".to_owned()),
            );

            let runtime = Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(1)
                .build()
                .expect("failed to build tokio runtime");

            runtime.block_on(browse_all_with_limit(self, root, None, max_routes))
        }
    }

    impl Browse for MockBrowser {
        async fn browse_node(&self, node_id: OpcUaNodeId) -> Result<Vec<OpcUaNode>> {
            Ok(self.graph.get(&node_id).cloned().unwrap_or_default())
        }
    }

    fn self_loop() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(1)]);
        (browser, nid(1))
    }

    fn direct_cycle() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2)]);
        browser.add_children(nid(2), vec![obj(1)]);
        (browser, nid(1))
    }

    fn long_cycle(len: usize) -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        for i in 1..=len {
            let Ok(i) = u32::try_from(i) else {
                break;
            };
            let next = if i as usize == len {
                1
            } else {
                i.saturating_add(1)
            };
            browser.add_children(nid(i), vec![obj(next)]);
        }
        (browser, nid(1))
    }

    fn diamond() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2), obj(3)]);
        browser.add_children(nid(2), vec![obj(4)]);
        browser.add_children(nid(3), vec![obj(4)]);
        (browser, nid(1))
    }

    fn deep_chain(n: u32) -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        for i in 1..n {
            browser.add_children(nid(i), vec![obj(i.saturating_add(1))]);
        }
        (browser, nid(1))
    }

    fn wide_tree(n: u32) -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        let children: Vec<_> = (2..=n.saturating_add(1)).map(obj).collect();
        browser.add_children(nid(1), children);
        (browser, nid(1))
    }

    fn mixed_class_cycle() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![var(2)]);
        browser.add_children(nid(2), vec![view(3)]);
        browser.add_children(nid(3), vec![obj(1)]);
        (browser, nid(1))
    }

    fn convergent_diamond_chain() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2), obj(3)]);
        browser.add_children(nid(2), vec![obj(4)]);
        browser.add_children(nid(3), vec![obj(4), obj(5)]);
        browser.add_children(nid(4), vec![obj(6)]);
        browser.add_children(nid(5), vec![obj(6)]);
        (browser, nid(1))
    }

    #[test]
    fn self_loop_errors() {
        let (browser, root) = self_loop();
        assert!(browser.browse(root, None).is_err());
    }

    #[test]
    fn direct_cycle_errors() {
        let (browser, root) = direct_cycle();
        assert!(browser.browse(root, None).is_err());
    }

    #[test]
    fn long_cycle_errors() {
        let (browser, root) = long_cycle(10);
        assert!(browser.browse(root, None).is_err());
    }

    #[test]
    fn mixed_class_cycle_errors_after_recursing_into_variable_and_view() {
        let (browser, root) = mixed_class_cycle();
        assert!(browser.browse(root, None).is_err());
    }

    #[test]
    fn empty_graph_returns_root_only() {
        let browser = MockBrowser::new();
        let result = browser
            .browse(nid(999), None)
            .expect("browse should succeed");

        assert_eq!(result.len(), 1);
        assert_eq!(route_paths(&result), vec!["/Root"]);
    }

    #[test]
    fn diamond_routes_are_ordinary_duplicate_subgraphs() {
        let (browser, root) = diamond();
        let graph = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(graph.len(), 5);
        assert_eq!(count_node_id(&graph, &nid(4)), 2);
        assert_eq!(
            route_paths(&graph),
            vec![
                "/Root",
                "/Root/Object_2",
                "/Root/Object_2/Object_4",
                "/Root/Object_3",
                "/Root/Object_3/Object_4",
            ]
        );
    }

    #[test]
    fn convergent_diamond_chain_duplicates_each_reached_route() {
        let (browser, root) = convergent_diamond_chain();
        let graph = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(count_node_id(&graph, &nid(4)), 2);
        assert_eq!(count_node_id(&graph, &nid(6)), 3);
    }

    #[test]
    fn duplicate_browse_paths_are_resolved_without_classification() {
        let mut browser = MockBrowser::new();
        let first = OpcUaNode::new(
            nid(2),
            "Sensor A".to_owned(),
            OpcUaNodeClass::Variable,
            OpcUaBrowseName::new(0, "Sensor".to_owned()),
        );
        let second = OpcUaNode::new(
            nid(3),
            "Sensor B".to_owned(),
            OpcUaNodeClass::Variable,
            OpcUaBrowseName::new(0, "Sensor".to_owned()),
        );
        browser.add_children(nid(1), vec![first, second]);

        let graph = browser.browse(nid(1), None).expect("browse should succeed");
        let matches = graph
            .resolve_path(&path(&["Root", "Sensor"]))
            .collect::<Vec<_>>();

        assert_eq!(matches.len(), 2);
    }

    #[test]
    fn resolve_path_returns_present_and_missing_routes() {
        let (browser, root) = diamond();
        let graph = browser.browse(root, None).expect("browse should succeed");

        let present = graph
            .resolve_path(&path(&["Root", "Object_2", "Object_4"]))
            .collect::<Vec<_>>();
        let missing = graph
            .resolve_path(&path(&["Root", "Object_9"]))
            .collect::<Vec<_>>();

        assert_eq!(present.len(), 1);
        assert!(missing.is_empty());
    }

    #[test]
    fn children_follow_route_edges_in_insertion_order() {
        let (browser, root) = diamond();
        let graph = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(
            child_paths(&graph, &path(&["Root"])),
            vec!["/Root/Object_2", "/Root/Object_3"]
        );
        assert_eq!(
            child_paths(&graph, &path(&["Root", "Object_2"])),
            vec!["/Root/Object_2/Object_4"]
        );
    }

    #[test]
    fn deep_chain_no_max_depth_returns_all_routes() {
        let chain_len = 50;
        let (browser, root) = deep_chain(chain_len);
        let graph = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(graph.len(), chain_len as usize);
    }

    #[test]
    fn max_depth_zero_returns_root_only() {
        let (browser, root) = deep_chain(10);
        let graph = browser
            .browse(root, Some(0))
            .expect("browse should succeed");

        assert_eq!(graph.len(), 1);
        assert_eq!(max_route_depth(&graph), 0);
    }

    #[test]
    fn max_depth_one_returns_root_children_only() {
        let (browser, root) = deep_chain(10);
        let graph = browser
            .browse(root, Some(1))
            .expect("browse should succeed");

        assert_eq!(graph.len(), 2);
        assert_eq!(max_route_depth(&graph), 1);
        assert_eq!(
            child_paths(&graph, &path(&["Root", "Object_2"])),
            Vec::<String>::new()
        );
    }

    #[test]
    fn deep_chain_reaches_exact_max_depth() {
        let max_depth = 5;
        let (browser, root) = deep_chain(20);
        let graph = browser
            .browse(root, Some(max_depth))
            .expect("browse should succeed");

        assert_eq!(max_route_depth(&graph), max_depth);
    }

    #[test]
    fn max_depth_greater_than_graph_depth_browses_entire_graph() {
        let chain_len = 10;
        let (browser, root) = deep_chain(chain_len);
        let graph = browser
            .browse(root, Some(15))
            .expect("browse should succeed");

        assert_eq!(graph.len(), chain_len as usize);
        assert_eq!(
            max_route_depth(&graph),
            chain_len.saturating_sub(1) as usize
        );
    }

    #[test]
    fn wide_tree_returns_all_root_children() {
        let width = 100;
        let (browser, root) = wide_tree(width);
        let graph = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(graph.len(), width as usize + 1);
        assert_eq!(child_paths(&graph, &path(&["Root"])).len(), width as usize);
    }

    #[test]
    fn route_ceiling_errors() {
        let (browser, root) = wide_tree(2);

        assert!(browser.browse_with_limit(root, 1).is_err());
    }

    #[test]
    fn variables_and_views_are_recursed() {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![var(2), view(3)]);
        browser.add_children(nid(2), vec![var(4)]);
        browser.add_children(nid(3), vec![obj(5)]);

        let graph = browser.browse(nid(1), None).expect("browse should succeed");

        assert_eq!(
            route_paths(&graph),
            vec![
                "/Root",
                "/Root/Variable_2",
                "/Root/Variable_2/Variable_4",
                "/Root/View_3",
                "/Root/View_3/Object_5",
            ]
        );
    }

    #[test]
    fn methods_and_other_nodes_are_leaves() {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![method(2), other(3), obj(4)]);
        browser.add_children(nid(2), vec![obj(5)]);
        browser.add_children(nid(3), vec![obj(6)]);
        browser.add_children(nid(4), vec![var(7)]);

        let graph = browser.browse(nid(1), None).expect("browse should succeed");

        assert!(child_paths(&graph, &path(&["Root", "Method_2"])).is_empty());
        assert!(child_paths(&graph, &path(&["Root", "Other_3"])).is_empty());
        assert_eq!(
            child_paths(&graph, &path(&["Root", "Object_4"])),
            vec!["/Root/Object_4/Variable_7"]
        );
    }
}
