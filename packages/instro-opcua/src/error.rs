use opcua::types as ua;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;

use crate::values::StatusCode;
use crate::values::UaType;

pyo3::create_exception!(instro.unstable._opcua, OpcUaError, PyException);

pub(crate) fn status(code: ua::StatusCode, message: impl Into<String>) -> PyErr {
    Python::attach(|py| {
        let error = OpcUaError::new_err(message.into());
        let attached = error
            .value(py)
            .setattr("status_code", StatusCode::new(code));

        match attached {
            Ok(()) => error,
            Err(failure) => failure,
        }
    })
}

pub(crate) fn native(error: ua::Error) -> PyErr {
    status(error.status(), error.to_string())
}

#[cfg(test)]
#[allow(clippy::expect_used)]
pub(crate) fn failure_status<T>(result: PyResult<T>) -> ua::StatusCode {
    let error = result.err().expect("operation unexpectedly succeeded");
    Python::attach(|py| {
        let value = error
            .value(py)
            .getattr("status_code")
            .expect("missing status code");

        *value
            .extract::<crate::values::StatusCode>()
            .expect("invalid status code")
            .as_ua_ref()
    })
}
