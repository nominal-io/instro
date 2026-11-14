mod client;
mod config;
mod error;
mod node;
mod shared_rt;
mod subscription;
mod time;
mod util;
mod values;

use pyo3::prelude::*;

#[pymodule]
fn _opcua(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("OpcUaError", module.py().get_type::<error::OpcUaError>())?;

    module.add_class::<time::Duration>()?;
    module.add_class::<time::DateTime>()?;
    values::register(module)?;
    config::register(module)?;

    module.add_class::<node::Node>()?;
    subscription::register(module)?;
    client::register(module)
}

#[cfg(test)]
mod test_support;
