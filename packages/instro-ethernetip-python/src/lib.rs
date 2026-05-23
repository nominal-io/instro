//! Python bindings for the Rust EtherNet/IP session API.

mod errors;
mod sync_session;
mod values;

use errors::EtherNetIpError;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use sync_session::EtherNetIpSession;
use values::{PlcKind, PlcValue, StructuredValue};

/// Initialize the private native EtherNet/IP extension module.
#[pymodule]
fn _ethernetip(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("EtherNetIpError", py.get_type::<EtherNetIpError>())?;
    m.add_class::<EtherNetIpSession>()?;
    m.add_class::<PlcKind>()?;
    m.add_class::<PlcValue>()?;
    m.add_class::<StructuredValue>()?;
    Ok(())
}
