pub(crate) mod attributes;
mod payload;
mod wrappers;

pub(crate) use attributes::PyAttributeValue;
use pyo3::prelude::*;
pub(crate) use wrappers::*;

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    wrappers::register(module)?;

    module.add_class::<PyAttributeValue>()
}
