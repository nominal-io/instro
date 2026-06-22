//! Python bindings for the Rust EtherNet/IP session API.

mod errors;
mod sync_session;
mod values;

use errors::{
    BatchTimeoutError, CipError, DataTypeMismatchError, EtherNetIpBatchError, EtherNetIpError,
    NetworkBatchError, OtherBatchError, SerializationError, TagNotFoundError, TagPathError,
};
use pyo3::prelude::*;
use pyo3::types::PyModule;
use sync_session::EtherNetIpSession;
use values::{PlcKind, PlcValue, StructuredValue};

/// Initialize the private native EtherNet/IP extension module.
#[pymodule]
fn _ethernetip(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("EtherNetIpError", py.get_type::<EtherNetIpError>())?;
    m.add(
        "EtherNetIpBatchError",
        py.get_type::<EtherNetIpBatchError>(),
    )?;
    m.add("TagNotFoundError", py.get_type::<TagNotFoundError>())?;
    m.add(
        "DataTypeMismatchError",
        py.get_type::<DataTypeMismatchError>(),
    )?;
    m.add("NetworkBatchError", py.get_type::<NetworkBatchError>())?;
    m.add("CipError", py.get_type::<CipError>())?;
    m.add("TagPathError", py.get_type::<TagPathError>())?;
    m.add("SerializationError", py.get_type::<SerializationError>())?;
    m.add("BatchTimeoutError", py.get_type::<BatchTimeoutError>())?;
    m.add("OtherBatchError", py.get_type::<OtherBatchError>())?;
    m.add_class::<EtherNetIpSession>()?;
    m.add_class::<PlcKind>()?;
    m.add_class::<PlcValue>()?;
    m.add_class::<StructuredValue>()?;
    Ok(())
}
