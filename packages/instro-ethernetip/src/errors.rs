use std::ffi::CString;

use instro_ethernetip_rs::{BatchReadError, Error};
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyRuntimeWarning};
use pyo3::prelude::*;

create_exception!(
    instro.unstable._ethernetip,
    EtherNetIpError,
    pyo3::exceptions::PyException,
    "EtherNet/IP operation failed."
);

create_exception!(
    instro.unstable._ethernetip,
    EtherNetIpBatchError,
    EtherNetIpError,
    "A single tag in a batched read failed. Base class for all batch-read variant exceptions."
);

create_exception!(
    instro.unstable._ethernetip,
    TagNotFoundError,
    EtherNetIpBatchError,
    "Per-tag batch read failure: the tag does not exist on the PLC."
);

create_exception!(
    instro.unstable._ethernetip,
    DataTypeMismatchError,
    EtherNetIpBatchError,
    "Per-tag batch read failure: the tag's actual type did not match the expected type. \
     Exposes `expected` and `actual` attributes."
);

create_exception!(
    instro.unstable._ethernetip,
    NetworkBatchError,
    EtherNetIpBatchError,
    "Per-tag batch read failure: a network-layer error occurred while reading this tag."
);

create_exception!(
    instro.unstable._ethernetip,
    CipError,
    EtherNetIpBatchError,
    "Per-tag batch read failure: the PLC returned a CIP protocol error. \
     Exposes `status` (u8) and `message` (str) attributes."
);

create_exception!(
    instro.unstable._ethernetip,
    TagPathError,
    EtherNetIpBatchError,
    "Per-tag batch read failure: the tag path could not be parsed or resolved."
);

create_exception!(
    instro.unstable._ethernetip,
    SerializationError,
    EtherNetIpBatchError,
    "Per-tag batch read failure: the value could not be serialized or deserialized."
);

create_exception!(
    instro.unstable._ethernetip,
    BatchTimeoutError,
    EtherNetIpBatchError,
    "Per-tag batch read failure: the operation timed out."
);

create_exception!(
    instro.unstable._ethernetip,
    OtherBatchError,
    EtherNetIpBatchError,
    "Per-tag batch read failure: an unclassified error occurred."
);

/// Convert a Rust session error into the Python exception shape exposed by this module.
///
/// In addition to the message text, the Python exception instance gets `operation`, `addr`, and
/// optional `tag_name` attributes so callers can branch on error context without parsing strings.
/// For per-tag batch failures, the returned exception is a typed subclass of
/// [`EtherNetIpBatchError`] that preserves the upstream variant and carries any variant-specific
/// attributes (e.g. `expected`/`actual` for [`DataTypeMismatchError`]).
pub(crate) fn map_error_with_py(py: Python<'_>, error: Error) -> PyErr {
    let message = error.to_string();

    match error {
        Error::CreateRuntime { .. } => PyRuntimeError::new_err(message),
        Error::BatchReadItem {
            addr,
            tag_name,
            source,
        } => map_batch_item(py, addr, tag_name, source),
        Error::Connect { addr, .. } => map_session_error(py, message, "connect", Some(addr), None),
        Error::ReadTag { addr, tag_name, .. }
        | Error::DecodeStructuredTag { addr, tag_name, .. }
        | Error::UnexpectedValueType { addr, tag_name, .. } => {
            map_session_error(py, message, "read_tag", Some(addr), Some(tag_name))
        }
        Error::BatchRead { addr, .. } => {
            map_session_error(py, message, "read_tags", Some(addr), None)
        }
        Error::WriteTag { addr, tag_name, .. } => {
            map_session_error(py, message, "write_tag", Some(addr), Some(tag_name))
        }
        Error::Unregister { addr, .. } => map_session_error(py, message, "close", Some(addr), None),
    }
}

fn map_session_error(
    py: Python<'_>,
    message: String,
    operation: &str,
    addr: Option<String>,
    tag_name: Option<String>,
) -> PyErr {
    let py_error = EtherNetIpError::new_err(message);
    set_common_attrs(py, &py_error, operation, addr, tag_name);
    py_error
}

fn map_batch_item(py: Python<'_>, addr: String, tag_name: String, source: BatchReadError) -> PyErr {
    let message = format!("failed to read tag '{tag_name}' from {addr}: {source}");

    let py_error = match &source {
        BatchReadError::TagNotFound(_) => TagNotFoundError::new_err(message),
        BatchReadError::DataTypeMismatch { .. } => DataTypeMismatchError::new_err(message),
        BatchReadError::Network(_) => NetworkBatchError::new_err(message),
        BatchReadError::Cip { .. } => CipError::new_err(message),
        BatchReadError::TagPath(_) => TagPathError::new_err(message),
        BatchReadError::Serialization(_) => SerializationError::new_err(message),
        BatchReadError::Timeout => BatchTimeoutError::new_err(message),
        BatchReadError::Other(_) => OtherBatchError::new_err(message),
    };

    set_common_attrs(py, &py_error, "read_tags", Some(addr), Some(tag_name));

    let exception = py_error.value(py);
    match &source {
        BatchReadError::DataTypeMismatch { expected, actual } => {
            if let Err(err) = exception.setattr("expected", expected.clone()) {
                warn_attr_set_failed(py, "expected", err);
            }
            if let Err(err) = exception.setattr("actual", actual.clone()) {
                warn_attr_set_failed(py, "actual", err);
            }
        }
        BatchReadError::Cip { status, message } => {
            if let Err(err) = exception.setattr("status", *status) {
                warn_attr_set_failed(py, "status", err);
            }
            if let Err(err) = exception.setattr("message", message.clone()) {
                warn_attr_set_failed(py, "message", err);
            }
        }
        _ => {}
    }

    py_error
}

fn set_common_attrs(
    py: Python<'_>,
    py_error: &PyErr,
    operation: &str,
    addr: Option<String>,
    tag_name: Option<String>,
) {
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
