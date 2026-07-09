//! Python bindings for the Rust circuit-solving engine.

use std::collections::HashMap;

use instro_simulation_physics_rs::{Attachment, Circuit as InnerCircuit, PsuMode};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyModule;

fn parse_mode(mode: &str) -> PyResult<PsuMode> {
    match mode.to_uppercase().as_str() {
        "CV" => Ok(PsuMode::Cv),
        "CC" => Ok(PsuMode::Cc),
        "UNREG" => Ok(PsuMode::Unreg),
        other => Err(PyValueError::new_err(format!(
            "unrecognized PSU mode {other:?}; allowed: \"CV\", \"CC\", \"UNREG\""
        ))),
    }
}

fn format_mode(mode: PsuMode) -> String {
    match mode {
        PsuMode::Cv => "CV".to_string(),
        PsuMode::Cc => "CC".to_string(),
        PsuMode::Unreg => "UNREG".to_string(),
    }
}

fn parse_attachment(kind: &str, params: &HashMap<String, f64>) -> PyResult<Attachment> {
    match kind {
        "resistive" => Ok(Attachment::Resistive {
            resistance_ohms: params.get("resistance").copied().unwrap_or(f64::INFINITY),
            emf_volts: params.get("emf").copied().unwrap_or(0.0),
        }),
        other => Err(PyValueError::new_err(format!(
            "unrecognized synthetic load kind {other:?}; allowed: \"resistive\""
        ))),
    }
}

#[pyclass(name = "Circuit")]
struct Circuit {
    inner: InnerCircuit,
}

#[pymethods]
impl Circuit {
    #[new]
    fn new() -> Self {
        Circuit {
            inner: InnerCircuit::new(),
        }
    }

    #[pyo3(signature = (id, r_series=0.0))]
    fn add_psu(&mut self, id: &str, r_series: f64) {
        self.inner.add_psu(id, r_series);
    }

    #[pyo3(signature = (psu_id, kind, **params))]
    fn attach_synthetic_load(
        &mut self,
        psu_id: &str,
        kind: &str,
        params: Option<HashMap<String, f64>>,
    ) -> PyResult<()> {
        let attachment = parse_attachment(kind, &params.unwrap_or_default())?;
        // Replace semantics, never append: the wiring layer calls this on every SCPI
        // query, and a same-shape replacement must allocate nothing (Correction 20).
        self.inner.set_psu_attachments(psu_id, vec![attachment]);
        Ok(())
    }

    fn set_psu_mode(&mut self, id: &str, mode: &str) -> PyResult<()> {
        self.inner.set_psu_mode(id, parse_mode(mode)?);
        Ok(())
    }

    fn set_psu_voltage_setpoint(&mut self, id: &str, volts: f64) {
        self.inner.set_psu_voltage_setpoint(id, volts);
    }

    fn set_psu_current_limit(&mut self, id: &str, amps: f64) {
        self.inner.set_psu_current_limit(id, amps);
    }

    #[pyo3(signature = (id, volts=f64::INFINITY))]
    fn set_psu_voltage_max(&mut self, id: &str, volts: f64) {
        self.inner.set_psu_voltage_max(id, volts);
    }

    fn set_psu_r_series(&mut self, id: &str, ohms: f64) {
        self.inner.set_psu_r_series(id, ohms);
    }

    fn set_psu_output_enabled(&mut self, id: &str, enabled: bool) {
        self.inner.set_psu_output_enabled(id, enabled);
    }

    fn set_psu_remote_sense(&mut self, id: &str, enabled: bool) {
        self.inner.set_psu_remote_sense(id, enabled);
    }

    fn step(&mut self, dt_seconds: f64) -> PyResult<()> {
        self.inner
            .step(dt_seconds)
            .map_err(|err| PyRuntimeError::new_err(err.to_string()))
    }

    fn psu_voltage(&self, id: &str) -> f64 {
        self.inner.psu_voltage(id)
    }

    fn psu_current(&self, id: &str) -> f64 {
        self.inner.psu_current(id)
    }

    fn psu_mode(&self, id: &str) -> String {
        format_mode(self.inner.psu_mode(id))
    }
}

/// Initialize the private native physics-engine extension module.
#[pymodule]
fn _physics(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Circuit>()?;
    Ok(())
}
