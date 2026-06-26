//! Recursive browsing of an OPC-UA server's address space.
//!
//! The [`Browse`] trait defines a single-level browse that returns the immediate
//! children of a given node. [`BrowseAll`] extends any `Browse` implementor
//! with a recursive depth-first traversal that:
//!
//! - **Detects cycles** via a `HashSet` — each node ID appears at most once in
//!   the result tree (first reference wins).
//! - **Limits depth** with an optional `max_depth` parameter.
//! - **Only recurses into `Object` nodes** — `Variable`, `Method`, and other
//!   node classes are kept as leaves.
//!
//! The [`OpcUaClient`](super::client::OpcUaClient) implementation of `Browse`
//! handles continuation points transparently, issuing `browse_next` calls until
//! all references for a node have been collected.

use std::collections::HashSet;
use std::future::Future;
use std::pin::Pin;

use anyhow::Result;
use open62541::ua;

use super::client::OpcUaClient;
use super::types::OpcUaNode;
use super::types::OpcUaNodeClass;
use super::types::OpcUaNodeId;

/// A trait for browsing a single node and returning its children.
pub trait Browse {
    /// Browse a single node and return its children.
    fn browse_node(&self, node_id: OpcUaNodeId) -> impl Future<Output = Result<Vec<OpcUaNode>>>;
}

/// A trait for browsing all nodes in a subtree and returning a list of all nodes.
pub trait BrowseAll: Browse {
    /// Browse all nodes in the subtree rooted at `node_id` and return a list of all nodes.
    ///
    /// The result is a nested tree of [`OpcUaNode`]. Each [`OpcUaNodeId`] appears at
    /// most once in the result tree, where the first reference wins.
    fn browse_all(
        &self,
        node_id: OpcUaNodeId,
        max_depth: Option<usize>,
    ) -> impl Future<Output = Result<Vec<OpcUaNode>>>;
}

impl<T: Browse> BrowseAll for T {
    async fn browse_all(
        &self,
        node_id: OpcUaNodeId,
        max_depth: Option<usize>,
    ) -> Result<Vec<OpcUaNode>> {
        let mut seen = HashSet::new();
        seen.insert(node_id.clone());
        browse_recursive(self, node_id, 0, max_depth, &mut seen).await
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

                Some(OpcUaNode {
                    node_id,
                    browse_name: reference.browse_name().name().to_string(),
                    display_name: reference.display_name().text().to_string(),
                    node_class,
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
    seen: &'a mut HashSet<OpcUaNodeId>,
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
            if !seen.insert(node.node_id.clone()) {
                continue;
            }

            if matches!(node.node_class, OpcUaNodeClass::Object) {
                node.children.extend(
                    browse_recursive(
                        browser,
                        node.node_id.clone(),
                        depth.saturating_add(1),
                        max_depth,
                        seen,
                    )
                    .await?,
                );
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
    use anyhow::bail;
    use tokio::runtime::Builder;

    use super::Browse;
    use super::browse_recursive;
    use crate::types::NodeIdInner;
    use crate::types::OpcUaNode;
    use crate::types::OpcUaNodeClass;
    use crate::types::OpcUaNodeId;

    fn nid(n: u32) -> OpcUaNodeId {
        OpcUaNodeId {
            namespace: 0,
            inner: NodeIdInner::Numeric(n),
        }
    }

    fn obj(id: u32) -> OpcUaNode {
        OpcUaNode {
            node_id: nid(id),
            browse_name: format!("Object_{id}"),
            display_name: format!("Object {id}"),
            node_class: OpcUaNodeClass::Object,
            children: Vec::new(),
        }
    }

    fn var(id: u32) -> OpcUaNode {
        OpcUaNode {
            node_id: nid(id),
            browse_name: format!("Variable_{id}"),
            display_name: format!("Variable {id}"),
            node_class: OpcUaNodeClass::Variable,
            children: Vec::new(),
        }
    }

    fn method(id: u32) -> OpcUaNode {
        OpcUaNode {
            node_id: nid(id),
            browse_name: format!("Method_{id}"),
            display_name: format!("Method {id}"),
            node_class: OpcUaNodeClass::Method,
            children: Vec::new(),
        }
    }

    fn collect_node_graph_helper(
        nodes: &[OpcUaNode],
        node_ids: &mut Vec<OpcUaNodeId>,
        seen: &mut HashSet<OpcUaNodeId>,
    ) -> Result<()> {
        for node in nodes {
            if !seen.insert(node.node_id.clone()) {
                bail!("duplicate node id: {node:?}");
            }

            node_ids.push(node.node_id.clone());

            collect_node_graph_helper(&node.children, node_ids, seen)?;
        }

        Ok(())
    }

    /// Collects all `OpcUaNodeId`s from a browse result tree via pre-order DFS and asserts that there are no
    /// duplicates. Results are topo-sorted.
    fn collect_node_graph(nodes: &[OpcUaNode]) -> Result<Vec<OpcUaNodeId>> {
        let mut node_ids = vec![];
        let mut seen = HashSet::new();
        collect_node_graph_helper(nodes, &mut node_ids, &mut seen)?;
        Ok(node_ids)
    }

    /// Counts the total number of nodes (including nested children) in the tree.
    fn count_nodes(nodes: &[OpcUaNode]) -> usize {
        nodes
            .iter()
            .map(|n| 1usize.saturating_add(count_nodes(&n.children)))
            .sum()
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
            let mut seen = HashSet::new();
            seen.insert(root.clone());
            let runtime = Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(1)
                .build()
                .expect("failed to build tokio runtime");

            runtime.block_on(browse_recursive(self, root, 0, max_depth, &mut seen))
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

    /// Cycle involving a non-Object node that should not be recursed:
    /// ```text
    ///   1(Object) -> 2(Variable) -> 3(Object) -> 1(Object)
    /// ```
    /// Because node 2 is a Variable, `browse_recursive` won't descend into it,
    /// so the cycle 3->1 is never reached.
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

    /// Object with both Object and non-Object children, where a non-Object
    /// child shares an id with an object node reached from another branch.
    /// The variable is listed first so id `2` is in `seen` before `obj(3)`
    /// is recursed; the later `obj(2)` reference from node 3 is omitted.
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
    fn self_loop_terminates() {
        let (browser, root) = self_loop();
        let result = browser.browse(root, None).expect("browse should succeed");

        // The root id is already in `seen`; the self-reference is the same id and is omitted.
        assert!(
            result.is_empty(),
            "self-edge should not add a duplicate node"
        );

        let ids = collect_node_graph(&result).expect("browse result should be acyclic");
        assert!(ids.is_empty());
    }

    #[test]
    fn direct_cycle_terminates() {
        let (browser, root) = direct_cycle();
        let result = browser.browse(root, None).expect("browse should succeed");

        // Root browse returns B. B's browse returns A, but A is already in `seen`.
        assert_eq!(result.len(), 1, "root should have one child (B)");
        let b = result.first().expect("root should have one child");
        assert_eq!(b.node_id, nid(2));

        // B was recursed into. B's browse_node returns A (obj(1)), but A is already in `seen`,
        // so that reference is omitted entirely.
        assert!(
            b.children.is_empty(),
            "back-edge to A should not appear as a second copy of node 1"
        );
    }

    #[test]
    fn long_cycle_terminates() {
        let cycle_len = 10usize;
        let (browser, root) = long_cycle(cycle_len);
        let result = browser.browse(root, None).expect("browse should succeed");

        let total = count_nodes(&result);
        // Ring 1 -> 2 -> ... -> cycle_len -> 1. Root 1 is not listed in the result; we walk
        // forward until the back-edge to 1, which is skipped as a duplicate. That yields
        // nodes 2..=cycle_len only (`cycle_len - 1` nodes).
        let expected = cycle_len.saturating_sub(1);
        assert_eq!(total, expected);
        let ids = collect_node_graph(&result).expect("browse result should be acyclic");
        assert_eq!(ids.len(), expected);
    }

    #[test]
    fn diamond_second_path_omits_duplicate_object() {
        let (browser, root) = diamond();
        let result = browser.browse(root, None).expect("browse should succeed");

        // 1 has children [2, 3]. Both 2 and 3 reference 4 in the mock graph; only the first
        // reference (under 2, in browse order) is kept.
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
        assert!(
            node3.children.is_empty(),
            "second reference to node 4 must be omitted"
        );

        let ids = collect_node_graph(&result).expect("unique ids");
        assert_eq!(
            ids.len(),
            3,
            "nodes 2, 3, and 4 once each; root 1 not in vec"
        );
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
    fn multi_cycle_terminates() {
        let (browser, root) = multi_cycle();
        let result = browser.browse(root, None).expect("browse should succeed");

        // browse_node(1) = [obj(2)]. Recurse into 2.
        // browse_node(2) = [obj(3), obj(4)]. Recurse into 3, then 4.
        // browse_node(3) = [obj(1)]. 1 already in `seen` -> omitted.
        // browse_node(4) = [obj(2)]. 2 already in `seen` -> omitted.
        //
        // Tree: [node2(children=[node3(leaf), node4(leaf)])] - three nodes total.
        let total = count_nodes(&result);
        assert_eq!(total, 3);

        // No node_id is expanded more than once.
        fn ids_with_children(nodes: &[OpcUaNode]) -> Vec<OpcUaNodeId> {
            let mut out = Vec::new();
            for n in nodes {
                if !n.children.is_empty() {
                    out.push(n.node_id.clone());
                }
                out.extend(ids_with_children(&n.children));
            }
            out
        }

        let recursed = ids_with_children(&result);
        let mut seen = HashSet::new();
        for id in &recursed {
            assert!(
                seen.insert(id.clone()),
                "node {id:?} was recursed into more than once"
            );
        }
    }

    #[test]
    fn mixed_class_cycle_no_recurse_into_variable() {
        let (browser, root) = mixed_class_cycle();
        let result = browser.browse(root, None).expect("browse should succeed");

        // browse_node(1) returns [var(2)]. Since node 2 is a Variable,
        // browse_recursive does NOT recurse into it.
        assert_eq!(result.len(), 1);
        assert_eq!(
            result
                .first()
                .expect("root should have one child")
                .node_class,
            OpcUaNodeClass::Variable
        );
        assert!(
            result
                .first()
                .expect("variable node should have no children")
                .children
                .is_empty(),
            "variable nodes should not be recursed into"
        );
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
    fn convergent_diamond_chain_no_id_reachable_twice_with_children() {
        let (browser, root) = convergent_diamond_chain();
        let result = browser.browse(root, None).expect("browse should succeed");

        // Verify no node is recursed into more than once: any node_id that
        // appears with non-empty children should appear only once with children.
        fn ids_with_children(nodes: &[OpcUaNode]) -> Vec<OpcUaNodeId> {
            let mut out = Vec::new();
            for n in nodes {
                if !n.children.is_empty() {
                    out.push(n.node_id.clone());
                }
                out.extend(ids_with_children(&n.children));
            }
            out
        }

        let recursed = ids_with_children(&result);
        let mut seen = HashSet::new();
        for id in &recursed {
            assert!(
                seen.insert(id.clone()),
                "node {id:?} was recursed into more than once"
            );
        }
    }

    #[test]
    fn variable_shadows_object_not_recursed() {
        let (browser, root) = variable_shadows_object();
        let result = browser.browse(root, None).expect("browse should succeed");

        // browse_node(1) = [var(2), obj(3)]
        // var(2): `seen.insert(2)` -> true, non-object -> kept; id 2 is in `seen`.
        // obj(3): `seen.insert(3)` -> true, object -> recurse.
        // browse_node(3) = [obj(2)]
        // obj(2): id 2 already in `seen` from var(2) -> reference omitted (no obj(4)).
        assert_eq!(result.len(), 2);

        let var_node = result.first().expect("root should have a child");
        assert_eq!(var_node.node_class, OpcUaNodeClass::Variable);
        assert!(var_node.children.is_empty());

        let obj_3 = result.get(1).expect("root should have two children");
        assert_eq!(obj_3.node_id, nid(3));
        assert!(
            obj_3.children.is_empty(),
            "obj(2) from node 3 is omitted — id 2 already listed as var(2)"
        );

        let ids = collect_node_graph(&result).expect("browse result should be acyclic");
        assert_eq!(ids.len(), 2, "variables 2 and object 3 only");
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
    fn convergent_paths_share_one_variable_reference() {
        let (browser, root) = convergent_variable_diamond();
        let result = browser.browse(root, None).expect("browse should succeed");

        assert_eq!(result.len(), 2);
        let node2 = result.first().expect("root children");
        let node3 = result.get(1).expect("root children");
        assert_eq!(node2.node_id, nid(2));
        assert_eq!(node3.node_id, nid(3));

        // Children are processed in `browse_node` order: obj(2) before obj(3), so
        // var(4) is kept under 2 and omitted under 3.
        assert_eq!(node2.children.len(), 1);
        assert_eq!(
            node2
                .children
                .first()
                .expect("variable under first branch")
                .node_id,
            nid(4)
        );
        assert!(
            node3.children.is_empty(),
            "second path must not list duplicate var(4)"
        );

        let ids = collect_node_graph(&result).expect("unique ids");
        assert_eq!(
            ids.len(),
            3,
            "root is not in the returned vec; obj(2), obj(3), var(4) each once"
        );
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
        browser.add_children(nid(1), vec![method(2), obj(3)]);
        browser.add_children(nid(2), vec![obj(4)]); // should never be reached
        browser.add_children(nid(3), vec![var(5)]);

        let result = browser.browse(nid(1), None).expect("browse should succeed");

        assert_eq!(result.len(), 2);

        let method_node = result
            .iter()
            .find(|n| n.node_id == nid(2))
            .expect("method node");
        assert!(
            method_node.children.is_empty(),
            "method nodes should not be recursed"
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
        let result = browser.browse(root, None).expect("browse should succeed");

        let total = count_nodes(&result);
        assert_eq!(
            total,
            cycle_len.saturating_sub(1),
            "back-edge to root is omitted; expect one fewer node than ring length"
        );
        collect_node_graph(&result).expect("browse result should be acyclic");
    }
}
