use opcua::types as ua;
use pyo3::exceptions::PyBaseException;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;

use crate::client::Node;
use crate::values::StatusCode;

pyo3::create_exception!(instro.unstable._opcua, OpcUaError, PyException);

pub fn status(code: ua::StatusCode, message: impl Into<String>, node: Option<Node>) -> PyErr {
    Python::attach(|py| {
        let error = OpcUaError::new_err(message.into());
        let value = error.value(py);
        let metadata = value
            .setattr("status_code", StatusCode(code))
            .and_then(|()| value.setattr("node", node));
        match metadata {
            Ok(()) => error,
            Err(failure) => failure,
        }
    })
}

pub fn native(error: ua::Error) -> PyErr {
    status(error.status(), error.to_string(), None)
}

pub fn context(
    py: Python<'_>,
    original: Option<&Bound<'_, PyBaseException>>,
    result: PyResult<()>,
) -> PyResult<bool> {
    match (original, result) {
        (Some(original), Err(cleanup)) => {
            let original = PyErr::from_value(original.clone().into_any());
            cleanup.set_context(py, original.context(py));
            original.set_context(py, Some(cleanup));
            Ok(false)
        }
        (_, result) => result.map(|()| false),
    }
}

#[cfg(test)]
mod tests {
    use pyo3::exceptions::PyKeyboardInterrupt;
    use pyo3::exceptions::PyRuntimeError;
    use pyo3::exceptions::PyValueError;

    use super::*;

    #[test]
    fn cleanup_preserves_keyboard_interrupt_and_existing_context() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let original = PyKeyboardInterrupt::new_err("interrupted");
            original.set_context(py, Some(PyValueError::new_err("earlier context")));
            let cleanup = status(ua::StatusCode::BadConnectionClosed, "cleanup failed", None);
            assert!(!context(py, Some(original.value(py)), Err(cleanup))?);
            assert!(original.is_instance_of::<PyKeyboardInterrupt>(py));
            let cleanup = original
                .context(py)
                .ok_or_else(|| PyRuntimeError::new_err("missing cleanup context"))?;
            assert!(cleanup.is_instance_of::<OpcUaError>(py));
            let earlier = cleanup
                .context(py)
                .ok_or_else(|| PyRuntimeError::new_err("missing original context"))?;
            assert!(earlier.is_instance_of::<PyValueError>(py));
            Ok(())
        })
    }

    #[test]
    fn operation_exception_preserves_status_bits_and_message() -> PyResult<()> {
        Python::initialize();
        Python::attach(|py| {
            let code = ua::StatusCode::from(0x803d_0480);
            let error = status(code, "request rejected", None);
            assert!(error.is_instance_of::<OpcUaError>(py));
            let value = error.value(py).cast::<OpcUaError>()?;
            assert_eq!(
                value.getattr("status_code")?.extract::<StatusCode>()?.0,
                code
            );
            assert!(value.getattr("node")?.is_none());
            assert_eq!(value.str()?.to_str()?, "request rejected");
            Ok(())
        })
    }
}
