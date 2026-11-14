pub(crate) mod attributes;
mod ordering;
mod payload;
mod wrappers;

pub(crate) use attributes::PyAttributeValue;
pub(crate) use wrappers::*;

pub(crate) trait UaType: Clone {
    type Inner: Clone;

    fn new(value: Self::Inner) -> Self;

    fn as_ua_ref(&self) -> &Self::Inner;
}

use pyo3::prelude::*;

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    wrappers::register(module)?;

    module.add_class::<PyAttributeValue>()
}
