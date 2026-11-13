use std::collections::HashMap;
use std::collections::HashSet;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::sync::mpsc;
use std::time::Duration as StdDuration;

use opcua::client::MonitoredItem;
use opcua::client::SubscriptionCallbacks;
use opcua::types as ua;
use pyo3::exceptions::PyBaseException;
use pyo3::exceptions::PyBaseExceptionGroup;
use pyo3::exceptions::PyRuntimeError;
use pyo3::exceptions::PyStopIteration;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyAny;
use tokio::sync::mpsc as async_mpsc;
use tokio::sync::watch;
use tokio::task::JoinHandle;

use crate::client::Connection;
use crate::client::Node;
use crate::client::runtime;
use crate::client::wait;
use crate::error::OpcUaError;
use crate::error::{self};
use crate::time::unix_timestamp;
use crate::values::DataValue;
use crate::values::ExtensionObject;
use crate::values::QualifiedName;
use crate::values::StatusCode;

#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct MonitoredItemSettings {
    parameters: ua::MonitoringParameters,
    mode: ua::MonitoringMode,
    index_range: ua::NumericRange,
    data_encoding: ua::QualifiedName,
}

#[pymethods]
impl MonitoredItemSettings {
    #[new]
    #[pyo3(signature = (*, sampling_interval=0.0, queue_size=0, discard_oldest=false, client_handle=0, filter=None, monitoring_mode=2, index_range=None, data_encoding=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        sampling_interval: f64,
        queue_size: u32,
        discard_oldest: bool,
        client_handle: u32,
        filter: Option<ExtensionObject>,
        monitoring_mode: u32,
        index_range: Option<String>,
        data_encoding: Option<QualifiedName>,
    ) -> PyResult<Self> {
        Ok(Self {
            parameters: ua::MonitoringParameters {
                sampling_interval,
                queue_size,
                discard_oldest,
                client_handle,
                filter: filter.map(|value| value.0).unwrap_or_default(),
            },
            mode: ua::MonitoringMode::try_from(monitoring_mode as i32)
                .map_err(|_| PyValueError::new_err("invalid monitoring_mode"))?,
            index_range: index_range
                .as_deref()
                .unwrap_or("")
                .parse()
                .map_err(|error: ua::NumericRangeError| PyValueError::new_err(error.to_string()))?,
            data_encoding: data_encoding.map(|value| value.0).unwrap_or_default(),
        })
    }
}

pub struct Settings {
    pub publishing_interval: StdDuration,
    pub lifetime_count: u32,
    pub max_keep_alive_count: u32,
    pub max_notifications_per_publish: u32,
    pub priority: u8,
    pub publishing_enabled: bool,
    pub sampling_interval: f64,
    pub queue_size: u32,
    pub discard_oldest: bool,
    pub poll_interval: Option<StdDuration>,
}

enum Input {
    Sample(ua::NodeId, ua::DataValue),
    End(ua::StatusCode),
}

enum Output {
    Sample(ua::NodeId, ua::DataValue),
    End(Option<PyErr>),
}

struct ServerSubscription {
    connection: Arc<Connection>,
    id: Option<u32>,
    stop: watch::Sender<bool>,
    producer: Option<JoinHandle<()>>,
}

impl ServerSubscription {
    async fn close(mut self) -> PyResult<()> {
        self.stop.send_replace(true);
        let stopped = match self.producer.take() {
            Some(producer) => producer
                .await
                .map_err(|error| PyRuntimeError::new_err(error.to_string())),
            None => Ok(()),
        };
        let Some(id) = self.id else {
            return stopped;
        };
        let deleted = self.connection.session.delete_subscription(id).await;
        self.id = None;
        stopped?;
        let status = deleted.map_err(error::native)?;
        if status.is_good() {
            Ok(())
        } else {
            Err(error::status(status, "subscription deletion failed"))
        }
    }
}

impl Drop for ServerSubscription {
    fn drop(&mut self) {
        let Some(id) = self.id.take() else {
            return;
        };
        self.stop.send_replace(true);
        let connection = self.connection.clone();
        let producer = self.producer.take();
        if let Ok(runtime) = runtime() {
            runtime.spawn(async move {
                if let Some(producer) = producer {
                    let _ = producer.await;
                }
                let _ = connection.session.delete_subscription(id).await;
            });
        }
    }
}

#[pyclass(frozen, module = "instro.unstable._opcua")]
pub struct Subscription {
    connection: Arc<Connection>,
    attribute: ua::AttributeId,
    output: Mutex<mpsc::Receiver<Output>>,
    server: Mutex<Option<ServerSubscription>>,
    finished: AtomicBool,
}

#[pymethods]
impl Subscription {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&self, py: Python<'_>) -> PyResult<(Node, DataValue)> {
        loop {
            if self.finished.load(Ordering::Acquire) {
                return Err(PyStopIteration::new_err(()));
            }
            let output = py.detach(|| {
                let receiver = self
                    .output
                    .lock()
                    .map_err(|_| PyRuntimeError::new_err("subscription queue lock poisoned"))?;
                Ok::<_, PyErr>(receiver.recv_timeout(StdDuration::from_millis(20)))
            })?;
            match output {
                Ok(Output::Sample(id, inner)) => {
                    return Ok((
                        Node::new(self.connection.clone(), id),
                        DataValue {
                            inner,
                            attribute: Some(self.attribute),
                        },
                    ));
                }
                Ok(Output::End(error)) => {
                    self.finished.store(true, Ordering::Release);
                    return Err(error.unwrap_or_else(|| PyStopIteration::new_err(())));
                }
                Err(mpsc::RecvTimeoutError::Timeout) => py.check_signals()?,
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    return Err(PyRuntimeError::new_err(
                        "subscription producer ended without a terminal result",
                    ));
                }
            }
        }
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        let server = self
            .server
            .lock()
            .map_err(|_| PyRuntimeError::new_err("subscription lifecycle lock poisoned"))?
            .take();
        match server {
            Some(server) => wait(py, server.close()),
            None => Ok(()),
        }
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(
        &self,
        py: Python<'_>,
        _kind: &Bound<'_, PyAny>,
        value: Option<&Bound<'_, PyBaseException>>,
        _traceback: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        error::context(py, value, self.close(py))
    }
}

pub async fn create(
    connection: Arc<Connection>,
    nodes: Vec<Node>,
    attribute: ua::AttributeId,
    settings: Settings,
    item_settings: Option<Vec<MonitoredItemSettings>>,
) -> PyResult<Subscription> {
    let mut ids = HashSet::new();
    for node in &nodes {
        if !Arc::ptr_eq(&node.connection, &connection) {
            return Err(PyValueError::new_err(
                "all Nodes must belong to this client",
            ));
        }
        if !ids.insert(node.id.clone()) {
            return Err(PyValueError::new_err(
                "a Node may occur only once in a subscription group",
            ));
        }
    }
    if settings
        .poll_interval
        .is_some_and(|interval| interval.is_zero())
    {
        return Err(PyValueError::new_err("poll_interval must be positive"));
    }
    if item_settings
        .as_ref()
        .is_some_and(|items| items.len() != nodes.len())
    {
        return Err(PyValueError::new_err(
            "item_settings must have one entry per Node",
        ));
    }
    let items = match item_settings {
        Some(items) => items,
        None => vec![
            MonitoredItemSettings {
                parameters: ua::MonitoringParameters {
                    sampling_interval: settings.sampling_interval,
                    queue_size: settings.queue_size,
                    discard_oldest: settings.discard_oldest,
                    ..Default::default()
                },
                mode: ua::MonitoringMode::Reporting,
                index_range: Default::default(),
                data_encoding: Default::default(),
            };
            nodes.len()
        ],
    };
    let requests: Vec<_> = nodes
        .iter()
        .zip(items)
        .map(|(node, item)| ua::MonitoredItemCreateRequest {
            item_to_monitor: ua::ReadValueId {
                node_id: node.id.clone(),
                attribute_id: attribute as u32,
                index_range: item.index_range,
                data_encoding: item.data_encoding,
            },
            monitoring_mode: item.mode,
            requested_parameters: item.parameters,
        })
        .collect();
    let reads: Vec<_> = requests
        .iter()
        .map(|request| request.item_to_monitor.clone())
        .collect();
    let (stop, stopped) = watch::channel(false);
    let status_stop = stopped.clone();
    let value_stop = stopped.clone();
    let (input, notifications) = async_mpsc::unbounded_channel();
    let status_input = input.clone();
    let callback = SubscriptionCallbacks::new(
        move |status| {
            if !*status_stop.borrow() {
                let _ = status_input.send(Input::End(status.status));
            }
        },
        move |value, item: &MonitoredItem| {
            if !*value_stop.borrow() {
                let _ = input.send(Input::Sample(item.item_to_monitor().node_id.clone(), value));
            }
        },
        |_, _| {},
    );
    let session = &connection.session;
    let id = session
        .create_subscription(
            settings.publishing_interval,
            settings.lifetime_count,
            settings.max_keep_alive_count,
            settings.max_notifications_per_publish,
            settings.priority,
            settings.publishing_enabled,
            callback,
        )
        .await
        .map_err(error::native)?;
    let mut server = ServerSubscription {
        connection: connection.clone(),
        id: Some(id),
        stop,
        producer: None,
    };
    let created = session
        .create_monitored_items(id, ua::TimestampsToReturn::Both, requests)
        .await;
    let failure = match created {
        Err(failure) => Some(error::native(failure)),
        Ok(results) => {
            let rejected: Vec<_> = nodes
                .into_iter()
                .zip(results)
                .filter(|(_, result)| !result.result.status_code.is_good())
                .map(|(node, result)| (node, result.result.status_code))
                .collect();
            if rejected.is_empty() {
                None
            } else {
                Some(Python::attach(|py| {
                    let errors: Vec<_> = rejected
                        .into_iter()
                        .map(|(node, code)| {
                            PyErr::new::<OpcUaError, _>((
                                "monitored item rejected",
                                StatusCode(code),
                                node,
                            ))
                            .into_value(py)
                        })
                        .collect();
                    PyBaseExceptionGroup::new_err(("subscription setup rejected", errors))
                }))
            }
        }
    };
    if let Some(failure) = failure {
        let cleanup = server.close().await;
        if let Err(cleanup) = cleanup {
            Python::attach(|py| failure.set_context(py, Some(cleanup)));
        }
        return Err(failure);
    }
    let (output, receiver) = mpsc::channel();
    let native_session = connection.session.clone();
    let completion = connection.completion.clone();
    server.producer = Some(tokio::spawn(produce(
        native_session,
        reads,
        notifications,
        completion,
        stopped,
        output,
        settings.poll_interval,
    )));
    Ok(Subscription {
        connection,
        attribute,
        output: Mutex::new(receiver),
        server: Mutex::new(Some(server)),
        finished: AtomicBool::new(false),
    })
}

fn poll_is_older(poll: &ua::DataValue, notification: &ua::DataValue) -> bool {
    let older = |poll, notification| matches!((poll, notification), (Some(poll), Some(notification)) if poll < notification);
    older(
        unix_timestamp(poll.source_timestamp, poll.source_picoseconds),
        unix_timestamp(
            notification.source_timestamp,
            notification.source_picoseconds,
        ),
    ) || older(
        unix_timestamp(poll.server_timestamp, poll.server_picoseconds),
        unix_timestamp(
            notification.server_timestamp,
            notification.server_picoseconds,
        ),
    )
}

async fn produce(
    session: Arc<opcua::client::Session>,
    reads: Vec<ua::ReadValueId>,
    mut input: async_mpsc::UnboundedReceiver<Input>,
    mut completion: watch::Receiver<Option<ua::StatusCode>>,
    mut stopped: watch::Receiver<bool>,
    output: mpsc::Sender<Output>,
    period: Option<StdDuration>,
) {
    let mut timer = period.map(|period| {
        let mut timer = tokio::time::interval_at(tokio::time::Instant::now() + period, period);
        timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        timer
    });
    let all_nodes: HashSet<_> = reads.iter().map(|read| read.node_id.clone()).collect();
    let mut quiet = all_nodes.clone();
    let mut pending = HashMap::new();
    let mut terminal = None;
    let failure = |code: ua::StatusCode| {
        (!code.is_good()).then(|| error::status(code, "native subscription terminated"))
    };
    loop {
        if let Some(code) = *completion.borrow() {
            terminal = failure(code);
            break;
        }
        if *stopped.borrow() {
            break;
        }
        tokio::select! {
            biased;
            changed = completion.changed() => {
                if changed.is_err() {
                    terminal = Some(PyRuntimeError::new_err("native session task ended without a status"));
                    break;
                }
            }
            _ = stopped.changed() => {}
            _ = async {
                match timer.as_mut() {
                    Some(timer) => { timer.tick().await; }
                    None => std::future::pending().await,
                }
            } => {
                for (id, value) in pending.drain() { let _ = output.send(Output::Sample(id, value)); }
                let selected: Vec<_> = reads.iter().filter(|read| quiet.contains(&read.node_id)).cloned().collect();
                if !selected.is_empty() {
                    match session.read(&selected, ua::TimestampsToReturn::Both, 0.0).await {
                        Ok(values) => pending.extend(selected.into_iter().zip(values).map(|(read, value)| (read.node_id, value))),
                        Err(error) => { terminal = Some(error::native(error)); break; }
                    }
                }
                quiet.clone_from(&all_nodes);
            }
            notification = input.recv() => match notification {
                Some(Input::Sample(id, value)) => {
                    quiet.remove(&id);
                    if let Some(poll) = pending.remove(&id) {
                        if poll_is_older(&poll, &value) { let _ = output.send(Output::Sample(id.clone(), poll)); }
                    }
                    let _ = output.send(Output::Sample(id, value));
                }
                Some(Input::End(code)) => { terminal = failure(code); break; }
                None => break,
            }
        }
    }
    if let Some(error) = (*completion.borrow()).and_then(failure) {
        terminal.get_or_insert(error);
    }
    while let Ok(message) = input.try_recv() {
        if let Input::End(code) = message {
            if let Some(error) = failure(code) {
                terminal.get_or_insert(error);
            }
        }
    }
    for (id, value) in pending {
        let _ = output.send(Output::Sample(id, value));
    }
    let _ = output.send(Output::End(terminal));
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<Subscription>()?;
    module.add_class::<MonitoredItemSettings>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn timestamped(source: Option<i64>, server: Option<i64>) -> ua::DataValue {
        ua::DataValue {
            source_timestamp: source.map(ua::DateTime::from),
            server_timestamp: server.map(ua::DateTime::from),
            ..Default::default()
        }
    }

    #[test]
    fn either_timestamp_pair_can_preserve_an_older_poll() {
        let early_source = timestamped(Some(10), Some(30));
        let late_source = timestamped(Some(20), Some(20));
        assert!(poll_is_older(&early_source, &late_source));
        assert!(poll_is_older(&late_source, &early_source));
    }

    #[test]
    fn equal_or_incomparable_timestamps_do_not_preserve_a_matching_poll() {
        let source_only = timestamped(Some(10), None);
        let server_only = timestamped(None, Some(20));
        assert!(!poll_is_older(&source_only, &source_only));
        assert!(!poll_is_older(&source_only, &server_only));
    }

    #[test]
    fn poll_timestamp_comparison_uses_normalized_nanoseconds() {
        let mut poll = timestamped(Some(10), None);
        poll.source_picoseconds = Some(100);
        let mut notification = poll.clone();
        notification.source_picoseconds = Some(199);
        assert!(!poll_is_older(&poll, &notification));
        notification.source_picoseconds = Some(200);
        assert!(poll_is_older(&poll, &notification));
    }
}
