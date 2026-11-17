mod client;
mod config;
mod error;
mod time;
mod util;
#[expect(
    dead_code,
    reason = "native decoding is consumed by the Node API upstack"
)]
mod values;

use pyo3::prelude::*;

#[pymodule]
fn _opcua(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("OpcUaError", module.py().get_type::<error::OpcUaError>())?;

    module.add_class::<time::Duration>()?;
    module.add_class::<time::DateTime>()?;
    values::register(module)?;
    config::register(module)?;

    client::register(module)
}
