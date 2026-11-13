use pyo3::prelude::*;
use pyo3::types::PyModule;

mod client;
mod config;
mod error;
mod subscription;
mod time;
mod values;

#[cfg(test)]
mod tests;

#[pymodule]
fn _opcua(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<time::DateTime>()?;
    module.add_class::<time::Duration>()?;
    values::register(module)?;
    config::register(module)?;
    client::register(module)?;
    subscription::register(module)?;
    module.add_class::<error::OpcUaError>()?;
    Ok(())
}
