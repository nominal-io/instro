use std::ffi::CString;

use instro_ethernetip_rs::Error;
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyRuntimeWarning};
use pyo3::prelude::*;

create_exception!(
    instro.unstable._ethernetip,     // module
    EtherNetIpError,                 // name
    pyo3::exceptions::PyException,   // base class
    "EtherNet/IP operation failed."  // doc
);

/// Convert a Rust session error into the Python exception shape exposed by this module.
///
/// In addition to the message text, the Python exception instance gets `operation`, `addr`, and
/// optional `tag_name` attributes so callers can branch on error context without parsing strings.
pub(crate) fn map_error_with_py(py: Python<'_>, error: Error) -> PyErr {
    let (operation, addr, tag_name) = match &error {
        Error::CreateRuntime { .. } => return PyRuntimeError::new_err(error.to_string()),
        Error::Connect { addr, .. } => ("connect", Some(addr.clone()), None),
        Error::ReadTag { addr, tag_name, .. }
        | Error::DecodeStructuredTag { addr, tag_name, .. }
        | Error::UnexpectedValueType { addr, tag_name, .. } => {
            ("read_tag", Some(addr.clone()), Some(tag_name.clone()))
        }
        Error::WriteTag { addr, tag_name, .. } => {
            ("write_tag", Some(addr.clone()), Some(tag_name.clone()))
        }
        Error::Unregister { addr, .. } => ("close", Some(addr.clone()), None),
    };

    let py_error = EtherNetIpError::new_err(error.to_string());
    let exception = py_error.value(py);
    if let Err(err) = exception.setattr("addr", addr) {
        warn_attr_set_failed(py, "addr", err);
    }
    if let Err(err) = exception.setattr("tag_name", tag_name) {
        warn_attr_set_failed(py, "tag_name", err);
    }
    if let Err(err) = exception.setattr("operation", operation) {
        warn_attr_set_failed(py, "operation", err);
    }
    py_error
}

// Helper to log a warning when an attribute set fails as a python warning.
fn warn_attr_set_failed(py: Python<'_>, attr: &str, err: PyErr) {
    let message =
        format!("failed to set EtherNetIpError.{attr} while constructing exception: {err}")
            .replace('\0', "\\0");
    let Ok(message) = CString::new(message) else {
        return;
    };
    let warning = py.get_type::<PyRuntimeWarning>();
    let _ = PyErr::warn(py, &warning, message.as_c_str(), 0);
}
