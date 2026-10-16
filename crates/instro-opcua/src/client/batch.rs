use itertools::Itertools as _;
use open62541::ua;

/// A batch of node-attribute pairs, for use when reading nodes in bulk.
#[derive(Debug, Clone)]
pub struct NodeReadBatch {
    pairs: Vec<(ua::NodeId, ua::AttributeId)>,
}

impl NodeReadBatch {
    /// Creates a new batch of nodes and attribute pairs by taking the cartesian product of the input iterators.
    /// i.e. for all nodes supplied, all attributes supplied will be read, for a total of total of
    /// `nodes.len() * attrs.len()` values when read.
    ///
    /// ## Example:
    /// ```
    /// use instro_opcua::prelude::*;
    /// use open62541::ua;
    ///
    /// let nodes = &[ua::NodeId::string(1, "Static"), ua::NodeId::string(2, "Active")];
    /// let attrs = &[ua::AttributeId::VALUE, ua::AttributeId::DESCRIPTION];
    /// let batch = NodeReadBatch::new_cartesian(nodes, attrs);
    /// assert_eq!(batch.len(), 4);
    /// assert!(!batch.is_empty());
    /// let mut node_iter = batch.pairs().iter();
    /// assert_eq!(node_iter.next().unwrap(), &(ua::NodeId::string(1, "Static"), ua::AttributeId::VALUE));
    /// assert_eq!(node_iter.next().unwrap(), &(ua::NodeId::string(1, "Static"), ua::AttributeId::DESCRIPTION));
    /// assert_eq!(node_iter.next().unwrap(), &(ua::NodeId::string(2, "Active"), ua::AttributeId::VALUE));
    /// assert_eq!(node_iter.next().unwrap(), &(ua::NodeId::string(2, "Active"), ua::AttributeId::DESCRIPTION));
    /// assert_eq!(node_iter.next(), None);
    /// ```
    pub fn new_cartesian<'nodes, 'attrs, N, A>(nodes: N, attrs: A) -> Self
    where
        N: IntoIterator<Item = &'nodes ua::NodeId>,
        A: IntoIterator<Item = &'attrs ua::AttributeId>,
    {
        let attrs = attrs.into_iter().collect_vec();
        let pairs = nodes
            .into_iter()
            .cloned()
            .cartesian_product(attrs.iter().map(|&attr| attr.clone()))
            .collect_vec();

        Self { pairs }
    }

    /// Constructs a batch from an already computed list of node-attribute pairs.
    ///
    /// ## Example:
    /// ```
    /// use instro_opcua::prelude::*;
    ///
    /// let batch = NodeReadBatch::new_from_pairs([
    ///     (ua::NodeId::string(1, "Static"), ua::AttributeId::VALUE),
    ///     (ua::NodeId::string(1, "Active"), ua::AttributeId::DESCRIPTION),
    /// ]);
    /// assert_eq!(batch.len(), 2);
    /// assert_eq!(&batch.nodes().collect::<Vec<_>>(), &[&ua::NodeId::string(1, "Static"), &ua::NodeId::string(1, "Active")]);
    /// assert_eq!(&batch.attrs().collect::<Vec<_>>(), &[&ua::AttributeId::VALUE, &ua::AttributeId::DESCRIPTION]);
    /// assert!(!batch.is_empty());
    /// ```
    pub fn new_from_pairs<I>(pairs: I) -> Self
    where
        I: IntoIterator<Item = (ua::NodeId, ua::AttributeId)>,
    {
        Self {
            pairs: pairs.into_iter().collect_vec(),
        }
    }

    /// Returns the list of nodes, used in read requests.
    pub fn nodes(&self) -> impl ExactSizeIterator<Item = &ua::NodeId> {
        self.pairs.iter().map(|pair| &pair.0)
    }

    /// Returns the attributes to be read for each node.
    pub fn attrs(&self) -> impl ExactSizeIterator<Item = &ua::AttributeId> {
        self.pairs.iter().map(|pair| &pair.1)
    }

    /// Returns the number of node-attribute pairs in the batch.
    pub const fn len(&self) -> usize {
        self.pairs.len()
    }

    /// Returns whether the batch is empty.
    pub const fn is_empty(&self) -> bool {
        self.pairs.is_empty()
    }

    /// Returns the list of nodes and attributes that will be read.
    pub const fn pairs(&self) -> &[(ua::NodeId, ua::AttributeId)] {
        self.pairs.as_slice()
    }

    /// Returns an iterator over the nodes and attributes that will be read.
    pub fn keys(&self) -> impl ExactSizeIterator<Item = (&ua::NodeId, &ua::AttributeId)> {
        self.pairs.iter().map(|pair| (&pair.0, &pair.1))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_cartesian_produces_correct_batch() {
        let nodes = &[
            ua::NodeId::string(1, "Static"),
            ua::NodeId::string(2, "Active"),
        ];
        let attrs = &[ua::AttributeId::VALUE, ua::AttributeId::DESCRIPTION];
        let batch = NodeReadBatch::new_cartesian(nodes, attrs);
        assert_eq!(batch.len(), 4);
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn batch_iter_iterates_over_all_node_attribute_pairs_in_order() -> Result<(), ()> {
        let nodes = [
            ua::NodeId::string(1, "Static"),
            ua::NodeId::string(2, "Active"),
        ];
        let attrs = [ua::AttributeId::VALUE];
        let batch = NodeReadBatch::new_cartesian(&nodes, &attrs);

        let mut iter = batch.keys();

        assert_eq!(iter.next(), Some((&nodes[0], &attrs[0])));
        assert_eq!(iter.next(), Some((&nodes[1], &attrs[0])));
        assert_eq!(iter.next(), None);

        Ok(())
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn batch_iter_doesnt_iterate_with_empty_lists() -> Result<(), ()> {
        let nodes = [
            ua::NodeId::string(1, "Static"),
            ua::NodeId::string(2, "Active"),
        ];
        let attrs = [ua::AttributeId::VALUE, ua::AttributeId::DESCRIPTION];

        assert_eq!(NodeReadBatch::new_cartesian(&nodes, []).keys().next(), None);
        assert_eq!(NodeReadBatch::new_cartesian([], &attrs).keys().next(), None);

        Ok(())
    }
}
