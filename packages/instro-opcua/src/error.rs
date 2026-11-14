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
            .setattr("status_code", <StatusCode as UaType>::new(code));

        match attached {
            Ok(()) => error,
            Err(failure) => failure,
        }
    })
}

pub(crate) fn native(error: ua::Error) -> PyErr {
    status(error.status(), error.to_string())
}
