mod client;
mod discover;

use pyo3::prelude::*;

#[pymodule(name = "_opcua")]
mod instro_opcua_py {
    #[pymodule_export]
    use super::client::DataPoint;
    #[pymodule_export]
    use super::client::ExpandedNodeId;
    #[pymodule_export]
    use super::client::NodeId;
    #[pymodule_export]
    use super::client::OpcUaClient;
    #[pymodule_export]
    use super::client::QualifiedName;
    #[pymodule_export]
    use super::client::Session;
    #[pymodule_export]
    use super::client::StatusCode;
    #[pymodule_export]
    use super::discover::EndpointDescription;
    #[pymodule_export]
    use super::discover::get_endpoints;
}
