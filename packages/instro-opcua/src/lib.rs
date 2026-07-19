mod client;
mod errors;
mod node;

use client::OpcUaClient;
use errors::OpcUaError;
use node::OpcUaNode;
use pyo3::prelude::*;
use pyo3::types::PyModule;

#[pymodule]
fn _opcua(py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("OpcUaError", py.get_type::<OpcUaError>())?;
    module.add_class::<OpcUaClient>()?;
    module.add_class::<OpcUaNode>()?;
    Ok(())
}
