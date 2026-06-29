use instro_ethernetip_rs::blocking::ExplicitSession;
use pyo3::exceptions::{PyResourceWarning, PyRuntimeError};
use pyo3::prelude::*;
use pyo3::types::PyAny;

use crate::errors::map_error_with_py;
use crate::values::{py_to_value, rust_value_to_py};

#[pyclass(module = "instro.unstable._ethernetip")]
/// Synchronous EtherNet/IP session wrapper for Python.
///
/// Instances own a single Rust `ExplicitSession` connected to one target address. The object is
/// stateful: once closed, later operations raise instead of implicitly reconnecting.
pub(crate) struct EtherNetIpSession {
    address: String,
    session: Option<ExplicitSession>,
}

impl EtherNetIpSession {
    /// Borrow the active Rust session or raise if the Python wrapper has been closed.
    ///
    /// Python code can call `close()` explicitly or leave a `with` block, after which all
    /// subsequent operations should fail consistently instead of trying to reconnect implicitly.
    fn session_mut(&mut self) -> PyResult<&mut ExplicitSession> {
        self.session
            .as_mut()
            .ok_or_else(|| PyRuntimeError::new_err("EtherNet/IP session is closed"))
    }
}

impl Drop for EtherNetIpSession {
    fn drop(&mut self) {
        if self.session.is_none() {
            return;
        }

        Python::try_attach(|py| {
            let resource_warning = py.get_type::<PyResourceWarning>();
            if let Err(error) = PyErr::warn(
                py,
                &resource_warning,
                c"unclosed EtherNetIpSession was garbage collected; call close() or use a context manager to unregister the session",
                1,
            ) {
                error.write_unraisable(py, None);
            }
        });
    }
}

#[pymethods]
impl EtherNetIpSession {
    #[new]
    #[pyo3(signature = (address, route_path_slots=None))]
    /// Connect to an EtherNet/IP endpoint such as `"192.168.1.10:44818"`.
    ///
    /// `route_path_slots`, when provided, is passed through as the upstream `rust-ethernet-ip`
    /// backplane slot route path.
    fn new(py: Python<'_>, address: &str, route_path_slots: Option<Vec<u8>>) -> PyResult<Self> {
        let session = if let Some(slots) = route_path_slots {
            py.detach(|| ExplicitSession::connect_with_route_path_slots(address, &slots))
                .map_err(|error| map_error_with_py(py, error))?
        } else {
            py.detach(|| ExplicitSession::connect(address))
                .map_err(|error| map_error_with_py(py, error))?
        };

        Ok(Self {
            address: address.to_owned(),
            session: Some(session),
        })
    }

    #[getter]
    /// Target address for this session.
    fn address(&self) -> &str {
        &self.address
    }

    #[getter]
    /// `True` once the session has been closed.
    fn closed(&self) -> bool {
        self.session.is_none()
    }

    /// Read a single PLC tag as a `PlcValue`.
    fn read_tag(&mut self, py: Python<'_>, name: &str) -> PyResult<Py<PyAny>> {
        let value = {
            let session = self.session_mut()?;
            py.detach(|| session.read_tag(name))
                .map_err(|error| map_error_with_py(py, error))?
        };

        rust_value_to_py(py, value)
    }

    /// Read several PLC tags in a single batched request, preserving input order.
    ///
    /// The returned list has one entry per requested tag. The second item of each tuple is
    /// either a `PlcValue` for successful reads or an `EtherNetIpError` instance for tags that
    /// failed individually (for example a missing tag or a type mismatch). Per-tag failures are
    /// returned as exception instances rather than raised, so a single bad tag does not throw
    /// away the values of the other tags in the batch. The call raises only when the entire
    /// batch could not be dispatched (for example a transport-level failure).
    fn read_tags(
        &mut self,
        py: Python<'_>,
        names: Vec<String>,
    ) -> PyResult<Vec<(String, Py<PyAny>)>> {
        let values = {
            let session = self.session_mut()?;
            py.detach(|| session.read_tags(&names))
                .map_err(|error| map_error_with_py(py, error))?
        };

        values
            .into_iter()
            .map(|(name, result)| {
                let item: Py<PyAny> = match result {
                    Ok(value) => rust_value_to_py(py, value)?,
                    Err(error) => map_error_with_py(py, error).into_value(py).into_any(),
                };
                Ok((name, item))
            })
            .collect()
    }

    /// Write a PLC tag from a `PlcValue` or another supported Python value.
    ///
    /// Bare Python `int`, `float`, and `str` inputs are rejected by the Python surface.
    /// Structured payloads can be passed directly as `StructuredValue`.
    fn write_tag(&mut self, py: Python<'_>, name: &str, value: &Bound<'_, PyAny>) -> PyResult<()> {
        let session = self.session_mut()?;
        let value = py_to_value(value)?;
        py.detach(|| session.write_tag(name, value))
            .map_err(|error| map_error_with_py(py, error))
    }

    /// Close the session. Calling this more than once is harmless.
    fn close(&mut self, py: Python<'_>) -> PyResult<()> {
        if let Some(session) = self.session.take() {
            py.detach(|| session.close())
                .map_err(|error| map_error_with_py(py, error))?;
        }

        Ok(())
    }

    /// Return the session itself for use in a `with` block.
    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    #[pyo3(signature = (_exc_type=None, _exc=None, _tb=None))]
    /// Close the session when leaving a `with` block without suppressing exceptions.
    fn __exit__(
        &mut self,
        py: Python<'_>,
        _exc_type: Option<&Bound<'_, PyAny>>,
        _exc: Option<&Bound<'_, PyAny>>,
        _tb: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<bool> {
        self.close(py)?;
        Ok(false)
    }

    fn __repr__(&self) -> String {
        format!(
            "EtherNetIpSession(address={:?}, closed={})",
            self.address,
            self.session.is_none()
        )
    }
}
