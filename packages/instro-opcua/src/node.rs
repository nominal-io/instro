use instro_opcua::types::OpcUaNode as _Node;
use instro_opcua::types::OpcUaNodeClass as _NodeClass;
use pyo3::prelude::*;

#[pyclass(frozen, module = "instro.unstable._opcua", skip_from_py_object)]
#[derive(Clone)]
pub(crate) struct OpcUaNode {
    inner: _Node,
}

impl From<_Node> for OpcUaNode {
    fn from(inner: _Node) -> Self {
        Self { inner }
    }
}

#[pymethods]
impl OpcUaNode {
    #[getter]
    fn node_id(&self) -> String {
        self.inner.node_id.to_string()
    }

    #[getter]
    fn browse_name(&self) -> &str {
        &self.inner.browse_name
    }

    #[getter]
    fn display_name(&self) -> &str {
        &self.inner.display_name
    }

    #[getter]
    fn node_class(&self) -> String {
        match &self.inner.node_class {
            _NodeClass::Object => "object".into(),
            _NodeClass::Variable => "variable".into(),
            _NodeClass::Method => "method".into(),
            _NodeClass::View => "view".into(),
            _NodeClass::Other(value) => format!("other:{value}"),
        }
    }

    #[getter]
    fn browse_path(&self) -> String {
        self.inner.browse_path.to_string()
    }

    #[getter]
    fn children(&self) -> Vec<Self> {
        self.inner
            .children
            .iter()
            .cloned()
            .map(Into::into)
            .collect()
    }

    fn __repr__(&self) -> String {
        format!(
            "OpcUaNode(node_id={:?}, browse_name={:?}, node_class={:?})",
            self.inner.node_id.to_string(),
            self.inner.browse_name,
            self.node_class(),
        )
    }
}

#[cfg(test)]
mod tests {
    use instro_opcua::types::NodeIdInner;
    use instro_opcua::types::OpcUaBrowseName;
    use instro_opcua::types::OpcUaBrowsePath;
    use instro_opcua::types::OpcUaNodeId;

    use super::*;

    #[test]
    fn wraps_the_core_node_type() {
        let node = OpcUaNode::from(_Node {
            node_id: OpcUaNodeId {
                namespace: 2,
                inner: NodeIdInner::String("temperature".into()),
            },
            browse_name: "Temperature".into(),
            display_name: "Temperature Sensor".into(),
            node_class: _NodeClass::Variable,
            browse_path: OpcUaBrowsePath::from_segment(OpcUaBrowseName::new(
                2,
                "Temperature".into(),
            )),
            children: Vec::new(),
        });

        assert_eq!(node.node_id(), "ns=2;s=temperature");
        assert_eq!(node.browse_name(), "Temperature");
        assert_eq!(node.display_name(), "Temperature Sensor");
        assert_eq!(node.node_class(), "variable");
        assert_eq!(node.browse_path(), "/2:Temperature");
        assert!(node.children().is_empty());
    }
}
