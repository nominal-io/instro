use pyo3::prelude::*;
use pyo3::types::PyModule;

#[pymodule]
fn _opcua(_module: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}
