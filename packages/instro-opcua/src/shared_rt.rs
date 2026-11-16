use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use tokio::runtime::Runtime;

static RUNTIME: PyOnceLock<Runtime> = PyOnceLock::new();

pub(crate) fn get(py: Python<'_>) -> PyResult<&'static Runtime> {
    RUNTIME.get_or_try_init(py, || {
        Runtime::new().map_err(|error| PyRuntimeError::new_err(error.to_string()))
    })
}
