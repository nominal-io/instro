//! Python bindings over quantus-client's blocking facade (PLAN.md Phase 5).
//!
//! Surface: `quantus.QuantusClient(config)` (path or JSON string) for
//! configuration/writes, `client.open_stream()` for a `StreamReader` whose
//! `next_event()` returns dicts with numpy arrays for sample data.

use instro_quantus_rs::blocking::QuantusClient as RustClient;
use instro_quantus_rs::config::{RackConfig, SettingValue};
use instro_quantus_rs::dbc::CanDecoder;
use instro_quantus_rs::error::Error;
use instro_quantus_rs::reconcile::ReconcileReport;
use instro_quantus_rs::stream::{StreamEngine, StreamEvent};
use numpy::IntoPyArray;
use pyo3::exceptions::{PyConnectionError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Mutex};
use std::time::Duration;

type DecoderMap = HashMap<i64, Arc<CanDecoder>>;

fn to_py_err(error: Error) -> PyErr {
    match error {
        Error::Config(message) => PyValueError::new_err(message),
        Error::Transport(message) => PyConnectionError::new_err(message),
        other => PyRuntimeError::new_err(other.to_string()),
    }
}

fn value_to_py(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
    Ok(match value {
        Value::Null => py.None(),
        Value::Bool(b) => b.into_pyobject(py)?.to_owned().unbind().into(),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.into_pyobject(py)?.unbind().into()
            } else {
                n.as_f64()
                    .unwrap_or(f64::NAN)
                    .into_pyobject(py)?
                    .unbind()
                    .into()
            }
        }
        Value::String(s) => s.into_pyobject(py)?.unbind().into(),
        Value::Array(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(value_to_py(py, item)?)?;
            }
            list.unbind().into()
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (key, item) in map {
                dict.set_item(key, value_to_py(py, item)?)?;
            }
            dict.unbind().into()
        }
    })
}

fn py_to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if let Ok(b) = obj.extract::<bool>() {
        return Ok(Value::Bool(b));
    }
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(Value::from(i));
    }
    if let Ok(f) = obj.extract::<f64>() {
        return Ok(Value::from(f));
    }
    if let Ok(s) = obj.extract::<String>() {
        return Ok(Value::String(s));
    }
    if let Ok(dict) = obj.cast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (key, item) in dict.iter() {
            map.insert(key.extract::<String>()?, py_to_value(&item)?);
        }
        return Ok(Value::Object(map));
    }
    if let Ok(list) = obj.cast::<PyList>() {
        let mut items = Vec::new();
        for item in list.iter() {
            items.push(py_to_value(&item)?);
        }
        return Ok(Value::Array(items));
    }
    Err(PyValueError::new_err(format!(
        "cannot convert {} to JSON",
        obj.get_type().name()?
    )))
}

fn report_to_py(py: Python<'_>, report: &ReconcileReport) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("version", &report.version)?;
    dict.set_item("restart_required", report.restart_required)?;
    dict.set_item("side_effects", report.side_effects.clone())?;
    dict.set_item("master_sampling_rate_hz", report.master_sampling_rate_hz)?;
    let modules = PyList::empty(py);
    for module in &report.modules {
        let m = PyDict::new(py);
        m.set_item("name", &module.name)?;
        m.set_item("item_id", module.item_id)?;
        m.set_item("requested_hz", module.requested_hz)?;
        m.set_item("achieved_hz", module.achieved_hz)?;
        m.set_item("divisor", module.divisor)?;
        modules.append(m)?;
    }
    dict.set_item("modules", modules)?;
    let channels = PyList::empty(py);
    for channel in &report.channels {
        let c = PyDict::new(py);
        c.set_item("alias", &channel.alias)?;
        c.set_item("item_id", channel.item_id)?;
        c.set_item("mode", channel.mode.clone())?;
        c.set_item("streaming", channel.streaming)?;
        c.set_item("sample_rate_hz", channel.sample_rate_hz)?;
        c.set_item("dbc", channel.dbc.clone())?;
        c.set_item("pulses_per_rev", channel.pulses_per_rev)?;
        channels.append(c)?;
    }
    dict.set_item("channels", channels)?;
    Ok(dict.unbind().into())
}

fn event_to_py(py: Python<'_>, event: StreamEvent, decoders: &DecoderMap) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    match event {
        StreamEvent::Analog(batch) => {
            dict.set_item("type", "analog")?;
            dict.set_item("channel_id", batch.channel_id)?;
            dict.set_item("timestamp_ns", batch.timestamp_ns)?;
            dict.set_item("integrity", batch.integrity)?;
            dict.set_item("min", batch.min)?;
            dict.set_item("max", batch.max)?;
            dict.set_item("received_ns", batch.received_unix_ns)?;
            dict.set_item("samples", batch.samples.into_pyarray(py))?;
        }
        StreamEvent::Tacho {
            channel_id,
            events_ms,
            received_unix_ns,
        } => {
            dict.set_item("type", "tacho")?;
            dict.set_item("channel_id", channel_id)?;
            dict.set_item("received_ns", received_unix_ns)?;
            dict.set_item("events_ms", events_ms.into_pyarray(py))?;
        }
        StreamEvent::Can {
            channel_id,
            frames,
            received_unix_ns,
        } => {
            dict.set_item("type", "can")?;
            dict.set_item("channel_id", channel_id)?;
            dict.set_item("received_ns", received_unix_ns)?;
            if let Some(decoder) = decoders.get(&i64::from(channel_id)) {
                // Decoded per-signal series: {name: {"timestamps_s": ..., "values": ...}}.
                let mut series: BTreeMap<String, (Vec<f64>, Vec<f64>)> = BTreeMap::new();
                let mut unknown_frames: u64 = 0;
                for frame in &frames {
                    match decoder.decode(frame.id, &frame.data) {
                        Some(values) => {
                            for (name, value) in values {
                                let (timestamps, samples) = series.entry(name).or_default();
                                timestamps.push(frame.timestamp_s);
                                samples.push(value);
                            }
                        }
                        None => unknown_frames += 1,
                    }
                }
                let signals = PyDict::new(py);
                for (name, (timestamps, values)) in series {
                    let s = PyDict::new(py);
                    s.set_item("timestamps_s", timestamps.into_pyarray(py))?;
                    s.set_item("values", values.into_pyarray(py))?;
                    signals.set_item(name, s)?;
                }
                dict.set_item("signals", signals)?;
                dict.set_item("unknown_frames", unknown_frames)?;
            } else {
                let list = PyList::empty(py);
                for frame in frames {
                    let f = PyDict::new(py);
                    f.set_item("timestamp_s", frame.timestamp_s)?;
                    f.set_item("id", frame.id)?;
                    f.set_item("frame_format", frame.frame_format)?;
                    f.set_item("frame_type", frame.frame_type)?;
                    f.set_item("data", PyBytes::new(py, &frame.data))?;
                    list.append(f)?;
                }
                dict.set_item("frames", list)?;
            }
        }
        StreamEvent::EpochRestart {
            sequence,
            received_unix_ns,
        } => {
            dict.set_item("type", "epoch_restart")?;
            dict.set_item("sequence", sequence)?;
            dict.set_item("received_ns", received_unix_ns)?;
        }
        StreamEvent::Gap {
            missing,
            received_unix_ns,
        } => {
            dict.set_item("type", "gap")?;
            dict.set_item("missing", missing)?;
            dict.set_item("received_ns", received_unix_ns)?;
        }
        StreamEvent::Skipped {
            channel_id,
            channel_type,
        } => {
            dict.set_item("type", "skipped")?;
            dict.set_item("channel_id", channel_id)?;
            dict.set_item("channel_type", channel_type)?;
        }
        StreamEvent::Disconnected { reason } => {
            dict.set_item("type", "disconnected")?;
            dict.set_item("reason", reason)?;
        }
    }
    Ok(dict.unbind().into())
}

/// Blocking client for a Quantus device: declarative configure + writes.
#[pyclass]
struct QuantusClient {
    inner: RustClient,
    host: String,
    /// CAN decoders by DBC path, loaded and validated at construction —
    /// BEFORE any hardware settings are written (a bad DBC must not leave a
    /// half-reconciled rack behind).
    decoders_by_path: HashMap<String, Arc<CanDecoder>>,
    /// CAN decoders by channel item_id, mapped at reconcile() (D14).
    decoders: Mutex<DecoderMap>,
}

#[pymethods]
impl QuantusClient {
    /// `config` is a path to a .json/.toml rack file, or inline JSON text.
    #[new]
    fn new(py: Python<'_>, config: &str) -> PyResult<Self> {
        let looks_like_path = !config.trim_start().starts_with('{');
        let rack = if looks_like_path {
            RackConfig::from_path(config)
        } else {
            RackConfig::from_json_str(config)
        }
        .map_err(to_py_err)?;
        let host = rack
            .connection
            .as_ref()
            .map(|c| c.host.clone())
            .ok_or_else(|| {
                PyValueError::new_err(
                    "no connection section in the rack config; pass one via the config",
                )
            })?;
        let mut decoders_by_path: HashMap<String, Arc<CanDecoder>> = HashMap::new();
        for module in &rack.modules {
            for channel in &module.channels {
                if let Some(dbc) = &channel.dbc
                    && !decoders_by_path.contains_key(dbc)
                {
                    let decoder = CanDecoder::from_path(dbc).map_err(to_py_err)?;
                    decoders_by_path.insert(dbc.clone(), Arc::new(decoder));
                }
            }
        }
        let inner = py.detach(|| RustClient::connect(rack)).map_err(to_py_err)?;
        Ok(QuantusClient {
            inner,
            host,
            decoders_by_path,
            decoders: Mutex::new(HashMap::new()),
        })
    }

    /// Write every declared setting, apply once, and return the report
    /// (achieved rates, alias -> item_id map, epoch impact). Also maps the
    /// construction-time CAN decoders onto the reported channel item ids.
    fn reconcile(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let report = py.detach(|| self.inner.reconcile()).map_err(to_py_err)?;
        let mut decoders: DecoderMap = HashMap::new();
        for channel in &report.channels {
            if let Some(dbc) = &channel.dbc {
                let decoder = match self.decoders_by_path.get(dbc) {
                    Some(decoder) => decoder.clone(),
                    None => Arc::new(CanDecoder::from_path(dbc).map_err(to_py_err)?),
                };
                decoders.insert(channel.item_id, decoder);
            }
        }
        *self.decoders.lock().unwrap() = decoders;
        report_to_py(py, &report)
    }

    fn discover(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let tree = py.detach(|| self.inner.discover()).map_err(to_py_err)?;
        let modules = PyList::empty(py);
        for module in &tree.modules {
            let m = PyDict::new(py);
            m.set_item("name", &module.name)?;
            m.set_item("item_id", module.item_id)?;
            m.set_item("channel_ids", module.channel_ids.clone())?;
            modules.append(m)?;
        }
        let dict = PyDict::new(py);
        dict.set_item("controller_id", tree.controller_id)?;
        dict.set_item("modules", modules)?;
        Ok(dict.unbind().into())
    }

    fn data_stream_setup(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let setup = py
            .detach(|| self.inner.data_stream_setup())
            .map_err(to_py_err)?;
        value_to_py(py, &setup)
    }

    /// Connect to the binary data stream and return a StreamReader.
    fn open_stream(&self, py: Python<'_>) -> PyResult<StreamReader> {
        let setup = py
            .detach(|| self.inner.data_stream_setup())
            .map_err(to_py_err)?;
        let port = setup
            .get("TCPPort")
            .and_then(Value::as_u64)
            .ok_or_else(|| PyRuntimeError::new_err("no TCPPort in dataStream/setup"))?
            as u16;
        let host = self.host.clone();
        let engine = py
            .detach(|| StreamEngine::connect(&host, port))
            .map_err(to_py_err)?;
        Ok(StreamReader {
            inner: Mutex::new(Some(engine)),
            decoders: self.decoders.lock().unwrap().clone(),
        })
    }

    /// Settings-plane write (D12): set values on one item and apply. Returns
    /// True when the streaming epoch restarts.
    fn write_settings(
        &self,
        py: Python<'_>,
        item_id: i64,
        values: &Bound<'_, PyDict>,
    ) -> PyResult<bool> {
        let mut map: BTreeMap<String, SettingValue> = BTreeMap::new();
        for (key, item) in values.iter() {
            let name: String = key.extract()?;
            let value = if let Ok(text) = item.extract::<String>() {
                SettingValue::Text(text)
            } else if let Ok(number) = item.extract::<f64>() {
                SettingValue::Number(number)
            } else {
                return Err(PyValueError::new_err(format!(
                    "setting '{name}' must be a string (enum description) or number"
                )));
            };
            map.insert(name, value);
        }
        py.detach(|| self.inner.write_settings(item_id, &map))
            .map_err(to_py_err)
    }

    #[pyo3(signature = (item_id=None))]
    fn auto_zero(&self, py: Python<'_>, item_id: Option<i64>) -> PyResult<()> {
        py.detach(|| self.inner.auto_zero(item_id))
            .map_err(to_py_err)
    }

    #[pyo3(signature = (item_id=None))]
    fn bridge_balance(&self, py: Python<'_>, item_id: Option<i64>) -> PyResult<()> {
        py.detach(|| self.inner.bridge_balance(item_id))
            .map_err(to_py_err)
    }

    #[pyo3(signature = (item_id=None))]
    fn bridge_balance_reset(&self, py: Python<'_>, item_id: Option<i64>) -> PyResult<()> {
        py.detach(|| self.inner.bridge_balance_reset(item_id))
            .map_err(to_py_err)
    }

    fn put_can_message_list(
        &self,
        py: Python<'_>,
        item_id: i64,
        messages: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let doc = py_to_value(messages)?;
        py.detach(|| self.inner.put_can_message_list(item_id, &doc))
            .map_err(to_py_err)
    }

    fn can_transmit(&self, py: Python<'_>, item_id: i64) -> PyResult<()> {
        py.detach(|| self.inner.can_transmit(item_id))
            .map_err(to_py_err)
    }

    fn suspend_stream(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.suspend_stream()).map_err(to_py_err)
    }

    fn resume_stream(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.resume_stream()).map_err(to_py_err)
    }
}

/// Reader over the binary data stream. `next_event()` blocks up to
/// `timeout_ms` and returns a dict (see event `type` field) or None.
#[pyclass]
struct StreamReader {
    inner: Mutex<Option<StreamEngine>>,
    decoders: DecoderMap,
}

#[pymethods]
impl StreamReader {
    #[pyo3(signature = (timeout_ms=1000))]
    fn next_event(&self, py: Python<'_>, timeout_ms: u64) -> PyResult<Option<Py<PyAny>>> {
        let received = py.detach(|| {
            let guard = self.inner.lock().unwrap();
            guard.as_ref().map(|engine| {
                engine
                    .events()
                    .recv_timeout(Duration::from_millis(timeout_ms))
            })
        });
        match received {
            None => Err(PyRuntimeError::new_err("stream is closed")),
            Some(Ok(event)) => Ok(Some(event_to_py(py, event, &self.decoders)?)),
            Some(Err(std::sync::mpsc::RecvTimeoutError::Timeout)) => Ok(None),
            Some(Err(std::sync::mpsc::RecvTimeoutError::Disconnected)) => {
                let dict = PyDict::new(py);
                dict.set_item("type", "disconnected")?;
                dict.set_item("reason", "reader thread ended")?;
                Ok(Some(dict.unbind().into()))
            }
        }
    }

    fn health(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let guard = self.inner.lock().unwrap();
        let engine = guard
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("stream is closed"))?;
        let health = engine.health();
        let dict = PyDict::new(py);
        dict.set_item("packets", health.packets)?;
        dict.set_item("gaps", health.gaps)?;
        dict.set_item("missing_packets", health.missing_packets)?;
        dict.set_item("epoch_restarts", health.epoch_restarts)?;
        dict.set_item("buffer_level", health.buffer_level)?;
        dict.set_item("transmit_timestamp_s", health.transmit_timestamp_s)?;
        Ok(dict.unbind().into())
    }

    fn close(&self, py: Python<'_>) {
        // detach: stop() joins the reader thread; holding the GIL
        // across that join would freeze every Python thread if the join is
        // slow (and next_event holds the same mutex for up to its timeout).
        py.detach(|| {
            if let Some(engine) = self.inner.lock().unwrap().take() {
                engine.stop();
            }
        });
    }
}

/// Parse and validate a rack config (path or inline JSON) without connecting.
#[pyfunction]
fn validate_config(config: &str) -> PyResult<()> {
    let looks_like_path = !config.trim_start().starts_with('{');
    let result = if looks_like_path {
        RackConfig::from_path(config)
    } else {
        RackConfig::from_json_str(config)
    };
    result.map(|_| ()).map_err(to_py_err)
}

#[pymodule]
fn _quantus(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<QuantusClient>()?;
    m.add_class::<StreamReader>()?;
    m.add_function(wrap_pyfunction!(validate_config, m)?)?;
    Ok(())
}
