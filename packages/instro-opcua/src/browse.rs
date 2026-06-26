//! Recursive browsing of an OPC-UA server's address space.
//!
//! The [`Browse`] trait defines a single-level browse that returns the immediate
//! children of a given node. [`BrowseAll`] extends any `Browse` implementor
//! with a recursive depth-first traversal that:
//!
//! - **Detects cycles** via the current ancestor path — diamonds are preserved,
//!   but a node ID already present in its own ancestry is reported as an error.
//! - **Limits depth** with an optional `max_depth` parameter.
//! - **Limits total browsed nodes** with a high defensive ceiling.
//! - **Recurses into `Object` and `Variable` nodes** — `Method`, `View`, and
//!   type nodes are kept as leaves.
//!
//! The [`OpcUaClient`](super::client::OpcUaClient) implementation of `Browse`
//! handles continuation points transparently, issuing `browse_next` calls until
//! all references for a node have been collected.

use std::collections::HashSet;
use std::future::Future;
use std::pin::Pin;

use anyhow::Context as _;
use anyhow::Result;
use anyhow::bail;
use open62541::ua;

use super::client::OpcUaClient;
use super::types::BrowsePath;
use super::types::OpcUaNode;
use super::types::OpcUaNodeClass;
use super::types::OpcUaNodeId;
use super::types::QualifiedBrowseName;

const DEFAULT_MAX_BROWSE_NODES: usize = 1_000_000;

/// A trait for browsing a single node and returning its children.
pub trait Browse {
    /// Browse a single node and return its children.
    fn browse_node(&self, node_id: OpcUaNodeId) -> impl Future<Output = Result<Vec<OpcUaNode>>>;
}

/// A trait for browsing all nodes in a subtree and returning a list of all nodes.
pub trait BrowseAll: Browse {
    /// Browse all nodes in the subtree rooted at `node_id` and return a list of all nodes.
    ///
    /// The result is a nested tree of [`OpcUaNode`]. A repeated [`OpcUaNodeId`] is
    /// allowed when reached through a different parent path, but a node ID that
    /// repeats in the current ancestry is treated as a cycle and returns an error.
    fn browse_all(
        &self,
        node_id: OpcUaNodeId,
        max_depth: Option<usize>,
    ) -> impl Future<Output = Result<Vec<OpcUaNode>>>;

    /// Browse all nodes below `node_id`, assigning returned children under
    /// `parent_path`.
    fn browse_all_from_path(
        &self,
        node_id: OpcUaNodeId,
        parent_path: BrowsePath,
        max_depth: Option<usize>,
    ) -> impl Future<Output = Result<Vec<OpcUaNode>>>;
}

impl<T: Browse> BrowseAll for T {
    async fn browse_all(
        &self,
        node_id: OpcUaNodeId,
        max_depth: Option<usize>,
    ) -> Result<Vec<OpcUaNode>> {
        self.browse_all_from_path(node_id, BrowsePath::default(), max_depth)
            .await
    }

    async fn browse_all_from_path(
        &self,
        node_id: OpcUaNodeId,
        parent_path: BrowsePath,
        max_depth: Option<usize>,
    ) -> Result<Vec<OpcUaNode>> {
        let mut ancestors = HashSet::new();
        ancestors.insert(node_id.clone());
        let mut visited = 0;
        browse_recursive(
            self,
            node_id,
            0,
            max_depth,
            parent_path,
            &mut ancestors,
            &mut visited,
            DEFAULT_MAX_BROWSE_NODES,
        )
        .await
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

                let Ok(node_id) = id.clone().try_into() else {
                    tracing::warn!(
                        target: "opcua::browse",
                        node_id = ?id,
                        "skipping reference during browse"
                    );

                    return None;
                };

                let node_class = OpcUaNodeClass::from(reference.node_class());
                let qualified_browse_name = QualifiedBrowseName {
                    namespace_index: reference.browse_name().namespace_index(),
                    name: reference.browse_name().name().to_string(),
                };

                Some(OpcUaNode {
                    node_id,
                    browse_name: qualified_browse_name.name.clone(),
                    display_name: reference.display_name().text().to_string(),
                    node_class,
                    browse_path: BrowsePath::from_segment(qualified_browse_name),
                    children: Vec::new(),
                })
            })
            .collect();

        Ok(nodes)
    }
}

// not taking a dep on futures just for this type
type BoxedFuture<'a, T> = Pin<Box<dyn Future<Output = Result<T>> + 'a>>;

/// Recursive DFS browse helper. Returns a list of nodes in the subtree rooted at `node_id`.
fn browse_recursive<'a, B: Browse>(
    browser: &'a B,
    node_id: OpcUaNodeId,
    depth: usize,
    max_depth: Option<usize>,
    parent_path: BrowsePath,
    ancestors: &'a mut HashSet<OpcUaNodeId>,
    visited: &'a mut usize,
    max_nodes: usize,
) -> BoxedFuture<'a, Vec<OpcUaNode>> {
    Box::pin(async move {
        if let Some(max_depth) = max_depth
            && depth >= max_depth
        {
            return Ok(Vec::new());
        }

        let raw = browser.browse_node(node_id).await?;
        let mut nodes = Vec::with_capacity(raw.len());

        for mut node in raw {
            if ancestors.contains(&node.node_id) {
                bail!(
                    "cycle detected while browsing node {} at path {}",
                    node.node_id,
                    parent_path
                );
            }

            *visited = visited.saturating_add(1);
            if *visited > max_nodes {
                bail!("browse exceeded maximum node count of {max_nodes}");
            }

            let segment = node
                .browse_path
                .segments()
                .last()
                .cloned()
                .with_context(|| {
                    format!("browse result for node {} had no browse path", node.node_id)
                })?;
            let node_path = parent_path.child(segment);
            node.browse_path = node_path.clone();

            if matches!(
                node.node_class,
                OpcUaNodeClass::Object | OpcUaNodeClass::Variable
            ) {
                ancestors.insert(node.node_id.clone());
                node.children.extend(
                    browse_recursive(
                        browser,
                        node.node_id.clone(),
                        depth.saturating_add(1),
                        max_depth,
                        node_path,
                        ancestors,
                        visited,
                        max_nodes,
                    )
                    .await?,
                );
                ancestors.remove(&node.node_id);
            }

            nodes.push(node);
        }

        Ok(nodes)
    })
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::collections::HashSet;

    use anyhow::Result;
    use tokio::runtime::Builder;

    use super::Browse;
    use super::BrowseAll;
    use super::DEFAULT_MAX_BROWSE_NODES;
    use super::browse_recursive;
    use crate::types::BrowsePath;
    use crate::types::NodeIdInner;
    use crate::types::OpcUaNode;
    use crate::types::OpcUaNodeClass;
    use crate::types::OpcUaNodeId;
    use crate::types::QualifiedBrowseName;

    fn nid(n: u32) -> OpcUaNodeId {
        OpcUaNodeId {
            namespace: 0,
            inner: NodeIdInner::Numeric(n),
        }
    }

    fn browse_path(namespace_index: u16, name: String) -> BrowsePath {
        BrowsePath::from_segment(QualifiedBrowseName::new(namespace_index, name))
    }

    fn obj(id: u32) -> OpcUaNode {
        let browse_name = format!("Object_{id}");
        OpcUaNode {
            node_id: nid(id),
            browse_name: browse_name.clone(),
            display_name: format!("Object {id}"),
            node_class: OpcUaNodeClass::Object,
            browse_path: browse_path(0, browse_name),
            children: Vec::new(),
        }
    }

    fn var(id: u32) -> OpcUaNode {
        let browse_name = format!("Variable_{id}");
        OpcUaNode {
            node_id: nid(id),
            browse_name: browse_name.clone(),
            display_name: format!("Variable {id}"),
            node_class: OpcUaNodeClass::Variable,
            browse_path: browse_path(0, browse_name),
            children: Vec::new(),
        }
    }

    fn method(id: u32) -> OpcUaNode {
        let browse_name = format!("Method_{id}");
        OpcUaNode {
            node_id: nid(id),
            browse_name: browse_name.clone(),
            display_name: format!("Method {id}"),
            node_class: OpcUaNodeClass::Method,
            browse_path: browse_path(0, browse_name),
            children: Vec::new(),
        }
    }

    fn view(id: u32) -> OpcUaNode {
        let browse_name = format!("View_{id}");
        OpcUaNode {
            node_id: nid(id),
            browse_name: browse_name.clone(),
            display_name: format!("View {id}"),
            node_class: OpcUaNodeClass::View,
            browse_path: browse_path(0, browse_name),
            children: Vec::new(),
        }
    }

    /// Counts the total number of nodes (including nested children) in the tree.
    fn count_nodes(nodes: &[OpcUaNode]) -> usize {
        nodes
            .iter()
            .map(|n| 1usize.saturating_add(count_nodes(&n.children)))
            .sum()
    }

    fn count_node_id(nodes: &[OpcUaNode], node_id: &OpcUaNodeId) -> usize {
        nodes
            .iter()
            .map(|node| {
                usize::from(&node.node_id == node_id)
                    .saturating_add(count_node_id(&node.children, node_id))
            })
            .sum()
    }

    fn collect_paths(nodes: &[OpcUaNode]) -> Vec<String> {
        nodes
            .iter()
            .flat_map(|node| {
                std::iter::once(node.browse_path.to_string()).chain(collect_paths(&node.children))
            })
            .collect()
    }

    /// Returns the maximum depth of the browse result tree (0 for empty, 1 for
    /// flat list of nodes with no children, etc.)
    fn max_tree_depth(nodes: &[OpcUaNode]) -> usize {
        if nodes.is_empty() {
            return 0;
        }
        nodes
            .iter()
            .map(|n| 1usize.saturating_add(max_tree_depth(&n.children)))
            .max()
            .unwrap_or(0)
    }

    /// A fake `Browser` implementation backed by an adjacency map.
    ///
    /// For each `OpcUaNodeId`, the map stores the list of `OpcUaBrowseNode`s
    /// that `browse_node` should return (these represent the immediate children
    /// of that node in the OPC UA address space).
    struct MockBrowser {
        graph: HashMap<OpcUaNodeId, Vec<OpcUaNode>>,
    }

    impl MockBrowser {
        fn new() -> Self {
            Self {
                graph: HashMap::new(),
            }
        }

        /// Registers `children` as the browse result for `parent`.
        fn add_children(&mut self, parent: OpcUaNodeId, children: Vec<OpcUaNode>) {
            self.graph.entry(parent).or_default().extend(children);
        }

        /// Convenience: run `browse_recursive` from `root` with the given
        /// `max_depth`, returning the result tree.
        fn browse(&self, root: OpcUaNodeId, max_depth: Option<usize>) -> Result<Vec<OpcUaNode>> {
            self.browse_with_parent(root, BrowsePath::default(), max_depth)
        }

        fn browse_with_parent(
            &self,
            root: OpcUaNodeId,
            parent_path: BrowsePath,
            max_depth: Option<usize>,
        ) -> Result<Vec<OpcUaNode>> {
            let mut ancestors = HashSet::new();
            ancestors.insert(root.clone());
            let mut visited = 0;
            let runtime = Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(1)
                .build()
                .expect("failed to build tokio runtime");

            runtime.block_on(browse_recursive(
                self,
                root,
                0,
                max_depth,
                parent_path,
                &mut ancestors,
                &mut visited,
                DEFAULT_MAX_BROWSE_NODES,
            ))
        }

        fn browse_with_limit(&self, root: OpcUaNodeId, max_nodes: usize) -> Result<Vec<OpcUaNode>> {
            let mut ancestors = HashSet::new();
            ancestors.insert(root.clone());
            let mut visited = 0;
            let runtime = Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(1)
                .build()
                .expect("failed to build tokio runtime");

            runtime.block_on(browse_recursive(
                self,
                root,
                0,
                None,
                BrowsePath::default(),
                &mut ancestors,
                &mut visited,
                max_nodes,
            ))
        }
    }

    impl Browse for MockBrowser {
        async fn browse_node(&self, node_id: OpcUaNodeId) -> Result<Vec<OpcUaNode>> {
            Ok(self.graph.get(&node_id).cloned().unwrap_or_default())
        }
    }

    /// A -> A (node references itself as a child).
    fn self_loop() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(1)]);
        (browser, nid(1))
    }

    /// A -> B -> A (two-node cycle).
    fn direct_cycle() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2)]);
        browser.add_children(nid(2), vec![obj(1)]);
        (browser, nid(1))
    }

    /// Ring of `len` nodes: 1 -> 2 -> 3 -> ... -> len -> 1.
    fn long_cycle(len: usize) -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        for i in 1..=len {
            let next = if i == len { 1 } else { i.saturating_add(1) };
            browser.add_children(nid(i as u32), vec![obj(next as u32)]);
        }
        (browser, nid(1))
    }

    /// Diamond:
    /// ```text
    ///     1
    ///    / \
    ///   2   3
    ///    \ /
    ///     4
    /// ```
    fn diamond() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2), obj(3)]);
        browser.add_children(nid(2), vec![obj(4)]);
        browser.add_children(nid(3), vec![obj(4)]);
        (browser, nid(1))
    }

    /// Linear chain: 1 -> 2 -> 3 -> ... -> n (all Object nodes).
    fn deep_chain(n: u32) -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        for i in 1..n {
            browser.add_children(nid(i), vec![obj(i.saturating_add(1))]);
        }
        (browser, nid(1))
    }

    /// Single root with `n` Object children (flat, one level deep).
    fn wide_tree(n: u32) -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        let children: Vec<_> = (2..=n.saturating_add(1)).map(obj).collect();
        browser.add_children(nid(1), children);
        (browser, nid(1))
    }

    /// Overlapping cycles:
    /// ```text
    ///       1 - 4
    ///      / \ /
    ///     2 - 3
    /// ```
    fn multi_cycle() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2), obj(4)]);
        browser.add_children(nid(2), vec![obj(3)]);
        browser.add_children(nid(3), vec![obj(1)]);
        browser.add_children(nid(4), vec![obj(3)]);
        (browser, nid(1))
    }

    /// Cycle involving a Variable node:
    /// ```text
    ///   1(Object) -> 2(Variable) -> 3(Object) -> 1(Object)
    /// ```
    fn mixed_class_cycle() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![var(2)]);
        browser.add_children(nid(2), vec![obj(3)]);
        browser.add_children(nid(3), vec![obj(1)]);
        (browser, nid(1))
    }

    /// Chain of diamonds sharing intermediate nodes:
    /// ```text
    ///     1
    ///    / \
    ///   1   4 - 6
    ///    \ / \ /
    ///     3 - 5
    /// ```
    fn convergent_diamond_chain() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2), obj(3)]);
        browser.add_children(nid(2), vec![obj(4)]);
        browser.add_children(nid(3), vec![obj(4), obj(5)]);
        browser.add_children(nid(4), vec![obj(6)]);
        browser.add_children(nid(5), vec![obj(6)]);
        (browser, nid(1))
    }

    /// Object with both Variable and Object children, where the Variable's id
    /// is also reachable through the Object branch.
    fn variable_shadows_object() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        // Root returns node 2 as a Variable, and node 3 as an Object.
        browser.add_children(nid(1), vec![var(2), obj(3)]);
        // Node 3 returns node 2 as an Object.
        browser.add_children(nid(3), vec![obj(2)]);
        // If browse_recursive visited node 2 as an Object, it would find node 4.
        browser.add_children(nid(2), vec![obj(4)]);
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
        let cycle_len = 10usize;
        let (browser, root) = long_cycle(cycle_len);
        assert!(browser.browse(root, None).is_err());
    }

    #[test]
    fn diamond_keeps_each_route() {
        let (browser, root) = diamond();
        let result = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(result.len(), 2);

        let node2 = result.first().expect("root should have two children");
        let node3 = result.get(1).expect("root should have two children");
        assert_eq!(node2.node_id, nid(2));
        assert_eq!(node3.node_id, nid(3));

        assert_eq!(node2.children.len(), 1);
        assert_eq!(
            node2
                .children
                .first()
                .expect("node2 should have a child")
                .node_id,
            nid(4)
        );
        assert_eq!(node3.children.len(), 1);
        assert_eq!(
            node3
                .children
                .first()
                .expect("node3 should have a child")
                .node_id,
            nid(4)
        );

        assert_eq!(count_node_id(&result, &nid(4)), 2);
    }

    #[test]
    fn deep_chain_respects_max_depth() {
        let chain_len = 20;
        let max_depth = 5;
        let (browser, root) = deep_chain(chain_len);
        let result = browser
            .browse(root, Some(max_depth))
            .expect("browse should succeed");

        let depth = max_tree_depth(&result);
        assert!(
            depth <= max_depth,
            "tree depth {depth} should not exceed max_depth {max_depth}"
        );
    }

    #[test]
    fn deep_chain_no_max_depth() {
        let chain_len = 50;
        let (browser, root) = deep_chain(chain_len);
        let result = browser.browse(root, None).expect("browse should succeed");

        // With no depth limit, we should get all nodes.
        let total = count_nodes(&result);
        // chain_len - 1 because the chain is 1->2->...->chain_len, and the
        // root (1) calls browse_node which returns 2..chain_len-1 in a chain.
        // Actually: deep_chain(n) creates edges 1->2, 2->3, ..., (n-1)->n.
        // browse_recursive(1) -> browse_node(1) = [obj(2)]
        //   recurse into 2 -> browse_node(2) = [obj(3)]
        //     ...
        //   recurse into (n-1) -> browse_node(n-1) = [obj(n)]
        //     recurse into n -> browse_node(n) = [] (no entry in map)
        // Total nodes: n-1 (nodes 2 through n)
        let expected = chain_len.saturating_sub(1) as usize;
        assert_eq!(
            total, expected,
            "should traverse all {expected} nodes in chain"
        );
    }

    #[test]
    fn wide_tree_returns_all_children() {
        let width = 100;
        let (browser, root) = wide_tree(width);
        let result = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(
            result.len(),
            width as usize,
            "all children should be returned"
        );
        for node in &result {
            assert!(
                node.children.is_empty(),
                "leaf objects should have no children"
            );
        }
    }

    #[test]
    fn multi_cycle_errors() {
        let (browser, root) = multi_cycle();
        assert!(browser.browse(root, None).is_err());
    }

    #[test]
    fn mixed_class_cycle_errors_after_recursing_into_variable() {
        let (browser, root) = mixed_class_cycle();
        assert!(browser.browse(root, None).is_err());
    }

    #[test]
    fn empty_graph_returns_empty() {
        let browser = MockBrowser::new();
        let result = browser
            .browse(nid(999), None)
            .expect("browse should succeed");
        assert!(result.is_empty());
    }

    #[test]
    fn browse_populates_paths_from_parent_path() {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2)]);
        browser.add_children(nid(2), vec![var(3)]);
        browser.add_children(nid(3), vec![var(4)]);

        let root_path = BrowsePath::from_segment(QualifiedBrowseName::new(0, "Root".into()));
        let result = browser
            .browse_with_parent(nid(1), root_path, None)
            .expect("browse should succeed");

        assert_eq!(
            collect_paths(&result),
            vec![
                "/Root/Object_2",
                "/Root/Object_2/Variable_3",
                "/Root/Object_2/Variable_3/Variable_4",
            ]
        );
    }

    #[test]
    fn browse_all_uses_empty_parent_path_by_default() {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2)]);

        let runtime = Builder::new_current_thread()
            .enable_all()
            .max_blocking_threads(1)
            .build()
            .expect("failed to build tokio runtime");
        let result = runtime
            .block_on(browser.browse_all(nid(1), None))
            .expect("browse should succeed");

        assert_eq!(collect_paths(&result), vec!["/Object_2"]);
    }

    #[test]
    fn browse_errors_when_node_ceiling_is_exceeded() {
        let (browser, root) = wide_tree(2);

        assert!(browser.browse_with_limit(root, 1).is_err());
    }

    #[test]
    fn convergent_diamond_chain_duplicates_per_route() {
        let (browser, root) = convergent_diamond_chain();
        let result = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(count_node_id(&result, &nid(4)), 2);
        assert_eq!(count_node_id(&result, &nid(6)), 3);
    }

    #[test]
    fn variable_and_object_with_same_id_are_kept_on_distinct_routes() {
        let (browser, root) = variable_shadows_object();
        let result = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(result.len(), 2);

        let var_node = result.first().expect("root should have a child");
        assert_eq!(var_node.node_class, OpcUaNodeClass::Variable);
        assert_eq!(var_node.children.len(), 1);

        let obj_3 = result.get(1).expect("root should have two children");
        assert_eq!(obj_3.node_id, nid(3));
        assert_eq!(obj_3.children.len(), 1);
        assert_eq!(
            obj_3.children.first().map(|node| &node.node_id),
            Some(&nid(2))
        );

        assert_eq!(count_node_id(&result, &nid(2)), 2);
        assert_eq!(count_node_id(&result, &nid(4)), 2);
    }

    /// Two object parents both reference the same variable id (convergent non-object).
    fn convergent_variable_diamond() -> (MockBrowser, OpcUaNodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2), obj(3)]);
        browser.add_children(nid(2), vec![var(4)]);
        browser.add_children(nid(3), vec![var(4)]);
        (browser, nid(1))
    }

    #[test]
    fn convergent_paths_duplicate_variable_reference() {
        let (browser, root) = convergent_variable_diamond();
        let result = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(result.len(), 2);
        let node2 = result.first().expect("root children");
        let node3 = result.get(1).expect("root children");
        assert_eq!(node2.node_id, nid(2));
        assert_eq!(node3.node_id, nid(3));

        assert_eq!(node2.children.len(), 1);
        assert_eq!(
            node2
                .children
                .first()
                .expect("variable under first branch")
                .node_id,
            nid(4)
        );
        assert_eq!(node3.children.len(), 1);
        assert_eq!(
            node3
                .children
                .first()
                .expect("variable under second branch")
                .node_id,
            nid(4)
        );

        assert_eq!(count_node_id(&result, &nid(4)), 2);
    }

    /// Regression test: a bug caused the depth counter to be decremented while
    /// simultaneously being incremented, so the effective depth never advanced
    /// and the browse stopped too shallow. This test asserts that when the chain
    /// is long enough to fill the requested depth, the tree depth is *exactly*
    /// `max_depth` — not just `<=`.
    #[test]
    fn deep_chain_reaches_exact_max_depth() {
        let chain_len = 20;
        let max_depth = 5;
        let (browser, root) = deep_chain(chain_len);
        let result = browser
            .browse(root, Some(max_depth))
            .expect("browse should succeed");

        let depth = max_tree_depth(&result);
        assert_eq!(
            depth, max_depth,
            "tree depth {depth} should be exactly max_depth {max_depth} when the chain is long \
             enough to fill it"
        );
    }

    /// Regression test: when `max_depth` exceeds the actual graph depth, the
    /// entire tree must be browsed. A bug that both incremented `depth` and
    /// decremented `max_depth` at each level would halve the effective reach
    /// (stopping at `ceil(max_depth / 2)`), silently truncating the tree even
    /// though `max_depth` was larger than the graph.
    ///
    /// Chain of 10 nodes (depth 9) with `max_depth = 15`:
    ///   - Correct:  effective limit = 15, full chain browsed -> 9 nodes.
    ///   - Buggy:    effective limit = ceil(15/2) = 8, last node lost -> 8 nodes.
    #[test]
    fn max_depth_greater_than_graph_depth_browses_entire_tree() {
        let chain_len = 10;
        let max_depth = 15; // well beyond the actual depth of 9
        let (browser, root) = deep_chain(chain_len);
        let result = browser
            .browse(root, Some(max_depth))
            .expect("browse should succeed");

        let expected_nodes = chain_len.saturating_sub(1) as usize; // nodes 2..=10
        let expected_depth = expected_nodes; // linear chain, depth == node count

        let total = count_nodes(&result);
        assert_eq!(
            total, expected_nodes,
            "all {expected_nodes} nodes should be browsed when max_depth ({max_depth}) exceeds \
             the graph depth ({expected_depth}), but only {total} were found"
        );

        let depth = max_tree_depth(&result);
        assert_eq!(
            depth, expected_depth,
            "tree depth should equal the full graph depth {expected_depth} when max_depth \
             ({max_depth}) is not a limiting factor, but was {depth}"
        );
    }

    #[test]
    fn max_depth_zero_returns_empty() {
        let (browser, root) = deep_chain(10);
        let result = browser
            .browse(root, Some(0))
            .expect("browse should succeed");
        assert!(result.is_empty(), "max_depth=0 should return no nodes");
    }

    #[test]
    fn max_depth_one_returns_flat_children() {
        let (browser, root) = deep_chain(10);
        let result = browser
            .browse(root, Some(1))
            .expect("browse should succeed");

        // max_depth=1, depth=0: 0 >= 1 is false, so browse_node(root) runs.
        // It returns [obj(2)]. Recurse into 2 with depth=1, max_depth=Some(0).
        // depth=1, max_depth=Some(0): 1 >= 0 is true -> return empty.
        // So node 2 has no children.
        assert_eq!(result.len(), 1);
        assert!(
            result
                .first()
                .expect("root should have a child")
                .children
                .is_empty(),
            "at max_depth=1, children should not be recursed into"
        );
    }

    #[test]
    fn method_nodes_not_recursed() {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![method(2), obj(3), view(6)]);
        browser.add_children(nid(2), vec![obj(4)]); // should never be reached
        browser.add_children(nid(3), vec![var(5)]);
        browser.add_children(nid(6), vec![obj(7)]); // should never be reached

        let result = browser.browse(nid(1), None).expect("browse should succeed");

        assert_eq!(result.len(), 3);

        let method_node = result
            .iter()
            .find(|n| n.node_id == nid(2))
            .expect("method node");
        assert!(
            method_node.children.is_empty(),
            "method nodes should not be recursed"
        );
        let view_node = result
            .iter()
            .find(|n| n.node_id == nid(6))
            .expect("view node");
        assert!(
            view_node.children.is_empty(),
            "view nodes should not be recursed"
        );

        let obj_node = result
            .iter()
            .find(|n| n.node_id == nid(3))
            .expect("object node");
        assert_eq!(
            obj_node.children.len(),
            1,
            "object node 3 should have var(5) as child"
        );
    }

    #[test]
    fn single_object_no_children() {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2)]);
        // node 2 has no entries in the map -> browse_node returns empty

        let result = browser.browse(nid(1), None).expect("browse should succeed");
        assert_eq!(result.len(), 1, "root should only have one child");
        assert_eq!(
            result.first().expect("root should have one child").node_id,
            nid(2),
            "root should have child node 2"
        );
        assert!(
            result
                .first()
                .expect("root should have one child")
                .children
                .is_empty(),
            "child node 2 should have no children"
        );
    }

    #[test]
    fn large_cycle_stress() {
        let cycle_len = 500;
        let (browser, root) = long_cycle(cycle_len);
        assert!(browser.browse(root, None).is_err());
    }
}
