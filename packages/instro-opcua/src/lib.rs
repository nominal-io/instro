mod client;
mod discover;
mod types;
mod util;

use pyo3::prelude::*;

pub(crate) use self::util::require_both;

#[pymodule(name = "_opcua")]
mod instro_opcua_py {
    #[pymodule_export]
    use super::client::Node;
    #[pymodule_export]
    use super::client::OpcUaClient;
    #[pymodule_export]
    use super::client::Session;
    #[pymodule_export]
    use super::discover::EndpointDescription;
    #[pymodule_export]
    use super::discover::get_endpoints;
    #[pymodule_export]
    use super::instro_opcua_py_types;
}

#[pymodule(name = "types")]
mod instro_opcua_py_types {
    #[pymodule_export]
    use super::types::AttributeId;
    #[pymodule_export]
    use super::types::BrowsePath;
    #[pymodule_export]
    use super::types::DataValue;
    #[pymodule_export]
    use super::types::ExpandedNodeId;
    #[pymodule_export]
    use super::types::MessageSecurityMode;
    #[pymodule_export]
    use super::types::NodeId;
    #[pymodule_export]
    use super::types::QualifiedName;
    #[pymodule_export]
    use super::types::RelativePath;
    #[pymodule_export]
    use super::types::RelativePathElement;
    #[pymodule_export]
    use super::types::StatusCode;
}
