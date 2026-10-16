//! # OPC UA Server Address Space Browsing
//!
//! ## The [`Browse`] & [`BrowseAll`] traits
//!
//! The [`Browse`] trait defines a single-level browse operation that returns the immediate
//! children of a given node. [`BrowseAll`] extends any [`Browse`] implementor with a default
//! iterative depth-first graph traversal implementation that:
//!
//! - **Detects cycles** via the current ancestor path — diamonds are preserved,
//!   but a node ID already present in its own ancestry is reported as an error.
//! - **Limits depth** with an optional `max_depth` parameter.
//! - **Limits total browsed nodes** with an optional `node_limit` parameter.
//! - **Descends into every returned node class**, including `Method`, `View`,
//!   and type nodes. [`BrowseAll::browse_all`] can therefore issue extra browse requests,
//!   return more descendants, and consume more of the depth and node-count
//!   limits.
//!
//! The [`Client`](super::client::Client) implementation of [`Browse`] handles continuation
//! points transparently, issuing [`browse_next`](open62541::AsyncClient::browse_next)
//! calls until all references for a node have been collected. It returns a nested tree of
//! [`BrowsedNode`]s, which includes the node ID, browse name, display name, node class, browse
//! path, and children.

use std::collections::HashSet;
use std::future::Future;

use itertools::Itertools;
use open62541::DataType as _;
use open62541::ua;

use super::client::Client;
use crate::Result;
use crate::error::ErrorExt as _;
use crate::error::UaErrorExt as _;
use crate::error::bail;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BrowsedNode {
    pub node_id: ua::NodeId,
    pub browse_name: String,
    pub display_name: String,
    pub node_class: ua::NodeClass,
    /// The namespace-qualified browse path to this node.
    pub browse_path: ua::RelativePath,
    pub children: Vec<BrowsedNode>,
}

/// A trait for browsing a single node and returning its children.
pub trait Browse {
    /// Browse a single node and return its children.
    fn browse_node(&self, node_id: ua::NodeId) -> impl Future<Output = Result<Vec<BrowsedNode>>>;
}

/// A trait for browsing all nodes in a subtree and returning a list of all nodes.
pub trait BrowseAll: Browse {
    /// Browse all nodes in the subtree rooted at `node_id` and return a list of all nodes.
    ///
    /// The result is a nested tree of [`BrowsedNode`]. A repeated [`ua::NodeId`] is
    /// allowed when reached through a different parent path, but a node ID that
    /// repeats in the current ancestry is treated as a cycle and returns an error.
    /// Every returned node class is recursed into, so this can issue extra browse
    /// requests and consume more of the depth and node-count limits.
    fn browse_all(
        &self,
        node_id: ua::NodeId,
        max_depth: Option<usize>,
        node_limit: Option<usize>,
    ) -> impl Future<Output = Result<Vec<BrowsedNode>>>;

    /// Browse all nodes below `node_id`, assigning returned children under
    /// `parent_path`.
    fn browse_all_from_path(
        &self,
        node_id: ua::NodeId,
        parent_path: ua::RelativePath,
        max_depth: Option<usize>,
        node_limit: Option<usize>,
    ) -> impl Future<Output = Result<Vec<BrowsedNode>>>;
}

impl<T: Browse> BrowseAll for T {
    async fn browse_all(
        &self,
        node_id: ua::NodeId,
        max_depth: Option<usize>,
        node_limit: Option<usize>,
    ) -> Result<Vec<BrowsedNode>> {
        self.browse_all_from_path(node_id, ua::RelativePath::init(), max_depth, node_limit)
            .await
    }

    async fn browse_all_from_path(
        &self,
        node_id: ua::NodeId,
        parent_path: ua::RelativePath,
        max_depth: Option<usize>,
        node_limit: Option<usize>,
    ) -> Result<Vec<BrowsedNode>> {
        browse_iterative(self, node_id, max_depth, node_limit, parent_path).await
    }
}

impl Browse for Client {
    async fn browse_node(&self, node_id: ua::NodeId) -> Result<Vec<BrowsedNode>> {
        let browse_desc = ua::BrowseDescription::default().with_node_id(&node_id);

        let (mut all_refs, mut cont_pt) = self
            .with_client(async |client| client.browse(&browse_desc).await)
            .await??;

        while let Some(cp) = cont_pt {
            let mut results = self
                .with_client(async |client| client.browse_next(&[cp]).await)
                .await??;

            match results.pop() {
                Some(result) => {
                    let (more_refs, next_cp) = result
                        .with_ua_context(|| format!("continuing browse of node '{node_id}'"))?;

                    all_refs.extend(more_refs);
                    cont_pt = next_cp;
                }

                None => break,
            }
        }

        let nodes = all_refs
            .into_iter()
            .map(|reference| {
                let node_id = reference.node_id().node_id().clone();

                let path_element = ua::RelativePathElement::init()
                    .with_include_subtypes(true)
                    .with_reference_type_id(&ua::NodeId::numeric(0, 33))
                    .with_is_inverse(!reference.is_forward())
                    .with_target_name(reference.browse_name());

                BrowsedNode {
                    node_id,
                    browse_name: reference.browse_name().name().to_string(),
                    node_class: reference.node_class(),
                    children: Vec::new(),
                    browse_path: ua::RelativePath::init().with_elements(&[path_element]),
                    display_name: reference.display_name().text().to_string(),
                }
            })
            .collect();

        Ok(nodes)
    }
}

fn append_path(parent_path: &ua::RelativePath, child: &mut BrowsedNode) -> Result<()> {
    let mut parent_path = parent_path.clone();
    let Some(existing) = parent_path.take_elements() else {
        return Ok(());
    };

    let segment = child.browse_path.iter().last().cloned().with_context(|| {
        format!(
            "browse result for node {} had no browse path",
            child.node_id
        )
    })?;

    // super inefficient, but idc really, browse api probably isn't going to stay
    let new_elements = existing
        .into_vec()
        .into_iter()
        .chain(std::iter::once(segment))
        .collect_vec();

    child.browse_path = parent_path.with_elements(&new_elements);

    Ok(())
}

/// Iteratively browse paths to all nodes, duplicating nodes where distinct
/// paths exist and erroring when a cycle is detected.
///
/// This algorithm works by colocating parents and children in the `nodes`
/// list, and maintaining a separate stack frame of `(index, limit)` pairs
/// which reference the section `nodes[index..limit]`. The `index` variable is
/// incremented until `limit` is reached, at which point the pair is popped and
/// all children (modeled as `nodes[limit..]`) of that node are split off and
/// appended to the parent in a single operation.
async fn browse_iterative<B: Browse>(
    browser: &B,
    node_id: ua::NodeId,
    max_depth: Option<usize>,
    node_limit: Option<usize>,
    parent_path: ua::RelativePath,
) -> Result<Vec<BrowsedNode>> {
    if let Some(0) = max_depth {
        return Ok(Vec::new());
    }

    let mut count_visited = 0_usize;

    let mut ancestors = HashSet::from([node_id.clone()]);
    let mut nodes = browser.browse_node(node_id.clone()).await?;
    let mut stack = Vec::from([(0_usize, nodes.len())]);

    for child in &mut nodes {
        append_path(&parent_path, child)?;
    }

    loop {
        let Some((current, limit)) = stack.last().copied() else {
            unreachable!("'stack' is only ever empty in 'else' case below");
        };

        if current < limit {
            let current_node = nodes
                .get(current)
                .context("browse stack referenced an out-of-bounds node index")?;

            if let Some(limit) = node_limit
                && count_visited >= limit
            {
                bail!(
                    internal,
                    "browsed node limit exceeded while browsing node '{}' (limit: {}, browse root \
                     node: '{}')",
                    current_node.node_id.clone(),
                    limit,
                    node_id.clone(),
                );
            }

            count_visited = count_visited.saturating_add(1);

            if ancestors.contains(&current_node.node_id) {
                bail!(
                    internal,
                    "browse cycle detected while browsing node '{}' (browse root node: '{}')",
                    current_node.node_id.clone(),
                    node_id.clone(),
                );
            } else {
                ancestors.insert(current_node.node_id.clone());
            }

            let mut new_children = if let Some(depth) = max_depth
                && stack.len() >= depth
            {
                tracing::debug!("Depth limit reached ({depth}) at node {current_node:?}");
                Vec::new()
            } else {
                browser.browse_node(current_node.node_id.clone()).await?
            };

            let parent_path = &current_node.browse_path;
            for child in &mut new_children {
                append_path(parent_path, child)?;
            }

            // Unconditional push is required to hit "else" case below, even if
            // `new_children` is empty. We could save a push/pop here at the
            // cost of duplicating cleanup logic
            stack.push((limit, limit.saturating_add(new_children.len())));
            nodes.extend(new_children);
        } else {
            stack.pop();

            // End case occurs when we've exhausted all nodes in the initial list
            let Some((prev, prev_limit)) = stack.last_mut() else {
                // Debloat the vec since we use it for computation in-place
                nodes.shrink_to_fit();
                return Ok(nodes);
            };

            let children = nodes.split_off(*prev_limit);

            let prev_node = nodes
                .get_mut(*prev)
                .context("browse stack referenced an out-of-bounds node index")?;

            prev_node.children = children;
            ancestors.remove(&prev_node.node_id);

            *prev = prev.saturating_add(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use open62541::DataType as _;
    use open62541::ua;
    use tokio::runtime::Builder;

    use super::Browse;
    use super::BrowseAll;
    use super::BrowsedNode;
    use super::browse_iterative;
    use crate::Result;

    fn nid(n: u32) -> ua::NodeId {
        ua::NodeId::numeric(0, n)
    }

    fn browse_path(namespace_index: u16, name: String) -> ua::RelativePath {
        ua::RelativePath::init().with_elements(&[ua::RelativePathElement::init()
            .with_include_subtypes(true)
            .with_reference_type_id(&ua::NodeId::numeric(0, 33))
            .with_is_inverse(false)
            .with_target_name(&ua::QualifiedName::new(namespace_index, &name))])
    }

    fn obj(id: u32) -> BrowsedNode {
        let browse_name = format!("Object_{id}");
        BrowsedNode {
            node_id: nid(id),
            browse_name: browse_name.clone(),
            display_name: format!("Object {id}"),
            node_class: ua::NodeClass::OBJECT,
            browse_path: browse_path(0, browse_name),
            children: Vec::new(),
        }
    }

    fn var(id: u32) -> BrowsedNode {
        let browse_name = format!("Variable_{id}");
        BrowsedNode {
            node_id: nid(id),
            browse_name: browse_name.clone(),
            display_name: format!("Variable {id}"),
            node_class: ua::NodeClass::VARIABLE,
            browse_path: browse_path(0, browse_name),
            children: Vec::new(),
        }
    }

    fn method(id: u32) -> BrowsedNode {
        let browse_name = format!("Method_{id}");
        BrowsedNode {
            node_id: nid(id),
            browse_name: browse_name.clone(),
            display_name: format!("Method {id}"),
            node_class: ua::NodeClass::METHOD,
            browse_path: browse_path(0, browse_name),
            children: Vec::new(),
        }
    }

    fn view(id: u32) -> BrowsedNode {
        let browse_name = format!("View_{id}");
        BrowsedNode {
            node_id: nid(id),
            browse_name: browse_name.clone(),
            display_name: format!("View {id}"),
            node_class: ua::NodeClass::VIEW,
            browse_path: browse_path(0, browse_name),
            children: Vec::new(),
        }
    }

    /// Counts the total number of nodes (including nested children) in the tree.
    fn count_nodes(nodes: &[BrowsedNode]) -> usize {
        nodes
            .iter()
            .map(|n| 1usize.saturating_add(count_nodes(&n.children)))
            .sum()
    }

    fn count_node_id(nodes: &[BrowsedNode], node_id: &ua::NodeId) -> usize {
        nodes
            .iter()
            .map(|node| {
                usize::from(&node.node_id == node_id)
                    .saturating_add(count_node_id(&node.children, node_id))
            })
            .sum()
    }

    fn collect_paths(nodes: &[BrowsedNode]) -> Vec<String> {
        nodes
            .iter()
            .flat_map(|node| {
                std::iter::once(node.browse_path.to_string()).chain(collect_paths(&node.children))
            })
            .collect()
    }

    /// Returns the maximum depth of the browse result tree (0 for empty, 1 for
    /// flat list of nodes with no children, etc.)
    fn max_tree_depth(nodes: &[BrowsedNode]) -> usize {
        if nodes.is_empty() {
            return 0;
        }
        nodes
            .iter()
            .map(|n| 1usize.saturating_add(max_tree_depth(&n.children)))
            .max()
            .unwrap_or(0)
    }

    /// A fake [`Browse`] implementation backed by an adjacency map.
    ///
    /// For each [`ua::NodeId`], the map stores the list of [`BrowsedNode`]s
    /// that [`Browse::browse_node`] should return (these represent the immediate children
    /// of that node in the OPC UA address space).
    struct MockBrowser {
        graph: HashMap<ua::NodeId, Vec<BrowsedNode>>,
    }

    impl MockBrowser {
        fn new() -> Self {
            Self {
                graph: HashMap::new(),
            }
        }

        /// Registers `children` as the browse result for `parent`.
        fn add_children(&mut self, parent: ua::NodeId, children: Vec<BrowsedNode>) {
            self.graph.entry(parent).or_default().extend(children);
        }

        /// Convenience: run `browse_iterative` from `root` with the given
        /// `max_depth`, returning the result tree.
        fn browse(&self, root: ua::NodeId, max_depth: Option<usize>) -> Result<Vec<BrowsedNode>> {
            self.browse_with_parent(root, ua::RelativePath::init(), max_depth)
        }

        fn browse_with_parent(
            &self,
            root: ua::NodeId,
            parent_path: ua::RelativePath,
            max_depth: Option<usize>,
        ) -> Result<Vec<BrowsedNode>> {
            let runtime = Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(1)
                .build()
                .expect("failed to build tokio runtime");

            runtime.block_on(browse_iterative(self, root, max_depth, None, parent_path))
        }

        fn browse_with_limit(
            &self,
            root: ua::NodeId,
            max_nodes: usize,
        ) -> Result<Vec<BrowsedNode>> {
            let runtime = Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(1)
                .build()
                .expect("failed to build tokio runtime");

            runtime.block_on(browse_iterative(
                self,
                root,
                None,
                Some(max_nodes),
                ua::RelativePath::init(),
            ))
        }
    }

    impl Browse for MockBrowser {
        async fn browse_node(&self, node_id: ua::NodeId) -> Result<Vec<BrowsedNode>> {
            Ok(self.graph.get(&node_id).cloned().unwrap_or_default())
        }
    }

    /// A -> A (node references itself as a child).
    fn self_loop() -> (MockBrowser, ua::NodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(1)]);
        (browser, nid(1))
    }

    /// A -> B -> A (two-node cycle).
    fn direct_cycle() -> (MockBrowser, ua::NodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2)]);
        browser.add_children(nid(2), vec![obj(1)]);
        (browser, nid(1))
    }

    /// Ring of `len` nodes: 1 -> 2 -> 3 -> ... -> len -> 1.
    fn long_cycle(len: usize) -> (MockBrowser, ua::NodeId) {
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
    fn diamond() -> (MockBrowser, ua::NodeId) {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2), obj(3)]);
        browser.add_children(nid(2), vec![obj(4)]);
        browser.add_children(nid(3), vec![obj(4)]);
        (browser, nid(1))
    }

    /// Linear chain: 1 -> 2 -> 3 -> ... -> n (all Object nodes).
    fn deep_chain(n: u32) -> (MockBrowser, ua::NodeId) {
        let mut browser = MockBrowser::new();
        for i in 1..n {
            browser.add_children(nid(i), vec![obj(i.saturating_add(1))]);
        }
        (browser, nid(1))
    }

    /// Single root with `n` Object children (flat, one level deep).
    fn wide_tree(n: u32) -> (MockBrowser, ua::NodeId) {
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
    fn multi_cycle() -> (MockBrowser, ua::NodeId) {
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
    fn mixed_class_cycle() -> (MockBrowser, ua::NodeId) {
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
    fn convergent_diamond_chain() -> (MockBrowser, ua::NodeId) {
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
    fn variable_shadows_object() -> (MockBrowser, ua::NodeId) {
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
        let result = browser
            .browse(root, None)
            .expect("failed to browse wide tree");

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
            .expect("failed to browse empty graph");
        assert!(result.is_empty());
    }

    #[test]
    fn browse_populates_paths_from_parent_path() {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![obj(2)]);
        browser.add_children(nid(2), vec![var(3)]);
        browser.add_children(nid(3), vec![var(4)]);

        let root_path = ua::RelativePath::init().with_elements(&[ua::RelativePathElement::init()
            .with_include_subtypes(true)
            .with_reference_type_id(&ua::NodeId::numeric(0, 33))
            .with_is_inverse(false)
            .with_target_name(&ua::QualifiedName::new(0, "Root"))]);

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
            .block_on(browser.browse_all(nid(1), None, None))
            .expect("browse should succeed");

        assert_eq!(collect_paths(&result), vec!["/Object_2"]);
    }

    #[test]
    fn browse_enforces_node_ceiling() {
        let (browser, root) = wide_tree(2);

        let result = browser
            .browse_with_limit(root.clone(), 2)
            .expect("browse up to node ceiling should succeed");

        assert_eq!(count_nodes(&result), 2);
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
        assert_eq!(var_node.node_class, ua::NodeClass::VARIABLE);
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
    fn convergent_variable_diamond() -> (MockBrowser, ua::NodeId) {
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
    fn all_node_types_are_recursed() {
        let mut browser = MockBrowser::new();
        browser.add_children(nid(1), vec![method(2), obj(3), view(6)]);
        browser.add_children(nid(2), vec![obj(4)]);
        browser.add_children(nid(3), vec![var(5)]);
        browser.add_children(nid(6), vec![obj(7)]);

        let result = browser.browse(nid(1), None).expect("browse should succeed");

        assert_eq!(result.len(), 3);

        let method_node = result
            .iter()
            .find(|n| n.node_id == nid(2))
            .expect("method node");

        assert!(
            !method_node.children.is_empty(),
            "method nodes should be recursed"
        );

        let view_node = result
            .iter()
            .find(|n| n.node_id == nid(6))
            .expect("view node");

        assert!(
            !view_node.children.is_empty(),
            "view nodes should be recursed"
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
