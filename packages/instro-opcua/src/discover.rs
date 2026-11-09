use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::types::EndpointDescription;

/// Helper function to discover endpoints for a given endpoint URL
#[pyfunction]
pub fn get_endpoints(endpoint_url: &str) -> PyResult<Vec<EndpointDescription>> {
    instro_opcua::client::get_endpoints(endpoint_url)
        .map(|endpoints| {
            endpoints
                .iter()
                .cloned()
                .map(EndpointDescription::from)
                .collect()
        })
        .map_err(|e| PyValueError::new_err(e.to_string()))
}
