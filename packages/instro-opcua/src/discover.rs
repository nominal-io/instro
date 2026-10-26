use instro_opcua::prelude::*;
use itertools::Itertools;
use pyo3::PyResult;
use pyo3::exceptions::PyValueError;
use pyo3::pyclass;
use pyo3::pyfunction;
use pyo3::pymethods;

/// Helper function to discover endpoints for a given endpoint URL
#[pyfunction]
pub fn get_endpoints(endpoint_url: &str) -> PyResult<Vec<EndpointDescription>> {
    instro_opcua::client::get_endpoints(endpoint_url)
        .map(|endpoints| endpoints.iter().cloned().map(EndpointDescription).collect())
        .map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyclass]
#[repr(transparent)]
pub struct EndpointDescription(ua::EndpointDescription);

#[pymethods]
impl EndpointDescription {
    #[getter]
    fn endpoint_url(&self) -> &str {
        self.0.endpoint_url().as_str().unwrap_or("")
    }

    #[getter]
    fn security_mode(&self) -> &str {
        match self.0.security_mode().into_raw().0 {
            ua::MessageSecurityMode::NONE_U32 => "none",
            ua::MessageSecurityMode::SIGN_U32 => "sign",
            ua::MessageSecurityMode::SIGNANDENCRYPT_U32 => "sign_and_encrypt",
            _ => unreachable!(),
        }
    }

    #[getter]
    fn security_policy(&self) -> &str {
        self.0.security_policy_uri().as_str().unwrap_or("")
    }

    #[getter]
    fn security_level(&self) -> u8 {
        self.0.security_level().into_raw()
    }

    #[getter]
    fn server_certificate(&self) -> &[u8] {
        self.0.server_certificate().as_bytes().unwrap_or(&[])
    }

    #[getter]
    fn application_uri(&self) -> &str {
        self.0.server().application_uri().as_str().unwrap_or("")
    }

    #[getter]
    fn product_uri(&self) -> &str {
        self.0.server().product_uri().as_str().unwrap_or("")
    }

    #[getter]
    fn gateway_server_uri(&self) -> &str {
        self.0.server().gateway_server_uri().as_str().unwrap_or("")
    }

    #[getter]
    fn discovery_profile_uri(&self) -> &str {
        self.0
            .server()
            .discovery_profile_uri()
            .as_str()
            .unwrap_or("")
    }

    #[getter]
    fn discovery_urls(&self) -> Vec<&str> {
        self.0
            .server()
            .discovery_urls()
            .unwrap_or(&[])
            .iter()
            .map(|s| s.as_str().unwrap_or(""))
            .collect_vec()
    }

    #[getter]
    fn application_name(&self) -> &str {
        self.0
            .server()
            .application_name()
            .text()
            .as_str()
            .unwrap_or("")
    }

    #[getter]
    fn application_type(&self) -> &str {
        match self.0.server().application_type().clone().into_raw().0 {
            ua::ApplicationType::SERVER_U32 => "server",
            ua::ApplicationType::CLIENT_U32 => "client",
            ua::ApplicationType::CLIENTANDSERVER_U32 => "client_and_server",
            ua::ApplicationType::DISCOVERYSERVER_U32 => "discovery_server",
            _ => unreachable!(),
        }
    }
}
