use anyhow::Result;
use pyo3::PyTypeInfo;
use pyo3::ToPyErr;
use pyo3::prelude::*;

pub trait ResultExt<T> {
    fn into_py<P>(self) -> PyResult<T>
    where
        P: ToPyErr + PyTypeInfo;
}

impl<T> ResultExt<T> for Result<T> {
    fn into_py<P>(self) -> PyResult<T>
    where
        P: ToPyErr + PyTypeInfo,
    {
        self.map_err(|err| PyErr::new::<P, _>(format!("{err:#}")))
    }
}

#[macro_export]
macro_rules! py_bail {
    ($t:ty, $($rest:tt)*) => {{ return Err(anyhow::anyhow!($($rest)*)).map_err(|e| PyErr::new::<$t, _>(format!("{e:#}"))) }};
}
