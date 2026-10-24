mod client;
mod discover;

use pyo3::prelude::*;

#[pymodule(name = "_opcua")]
mod instro_opcua_py {
    #[pymodule_export]
    use super::{
        discover::{get_endpoints, EndpointDescription},
        client::OpcUaClient,
        client::Session,
        client::NodeId,
        client::DataPoint,
        client::QualifiedName,
        client::StatusCode,
        client::ExpandedNodeId,
    };
}
