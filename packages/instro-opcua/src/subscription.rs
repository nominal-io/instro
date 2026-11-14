use std::collections::HashSet;
use std::sync::Mutex;
use std::sync::mpsc;
use std::time::Duration;

use opcua::client::MonitoredItem;
use opcua::client::Session;
use opcua::client::SubscriptionCallbacks;
use opcua::types as ua;
use pyo3::exceptions::PyBaseException;
use pyo3::exceptions::PyRuntimeError;
use pyo3::exceptions::PyStopIteration;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use tokio::runtime::Handle;
use tokio::sync::mpsc as async_mpsc;
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

use crate::client::OpcUaClient;
use crate::config::DurationInput;
use crate::config::NativeInteger;
use crate::error;
use crate::node;
use crate::node::Node;
use crate::node::NodeInput;
use crate::util;
use crate::values::PyAttributeValue;

mod producer;
use producer::Input;
use producer::Output;
use producer::Producer;

struct Settings {
    publishing_interval: Duration,
    lifetime_count: u32,
    max_keep_alive_count: u32,
    max_notifications_per_publish: u32,
    priority: u8,
    publishing_enabled: bool,
    sampling_interval: f64,
    queue_size: u32,
    discard_oldest: bool,
    poll_interval: Option<Duration>,
}

struct Worker {
    stop: Option<oneshot::Sender<()>>,
    task: JoinHandle<()>,
}

impl Worker {
    async fn stop(&mut self) -> PyResult<()> {
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(());
        }

        (&mut self.task)
            .await
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))
    }
}

struct Cleanup {
    client: OpcUaClient,
    id: Option<u32>,
    worker: Option<Worker>,
    runtime: Handle,
}

impl Cleanup {
    async fn close(mut self) -> PyResult<()> {
        let stopped = match self.worker.as_mut() {
            Some(worker) => worker.stop().await,
            None => Ok(()),
        };

        self.worker = None;

        let deleted = match self.id {
            None => Ok(()),

            Some(id) => {
                let result = self
                    .client
                    .with_session(async |session| delete(session, id).await)
                    .await;

                self.id = None;

                result
            }
        };

        match (stopped, deleted) {
            (Err(stopped), Err(deleted)) => {
                Python::attach(|py| stopped.set_context(py, Some(deleted)));
                Err(stopped)
            }

            (Err(error), _) | (_, Err(error)) => Err(error),
            _ => Ok(()),
        }
    }
}

impl Drop for Cleanup {
    fn drop(&mut self) {
        let Some(id) = self.id.take() else {
            return;
        };

        let cleanup = Self {
            client: self.client.clone(),
            id: Some(id),
            worker: self.worker.take(),
            runtime: self.runtime.clone(),
        };

        self.runtime.spawn(async move {
            if let Err(error) = cleanup.close().await {
                tracing::warn!(%error, "OPC UA subscription finalization failed");
            }
        });
    }
}

async fn delete(session: &Session, id: u32) -> PyResult<()> {
    let status = session
        .delete_subscription(id)
        .await
        .map_err(error::native)?;

    if status.is_good() {
        Ok(())
    } else {
        Err(error::status(status, "subscription deletion failed"))
    }
}

/// An iterator of ``(Node, AttributeValue)`` samples for one selected attribute.
///
/// Create with ``client.subscribe()`` and use a context manager or ``close()``
/// for synchronous cleanup. Buffered samples remain iterable after close.
/// A Python signal interrupts only the current wait, allowing iteration to resume.
#[pyclass(frozen, module = "instro.unstable._opcua")]
pub(crate) struct Subscription {
    attribute: ua::AttributeId,
    output: Mutex<Option<mpsc::Receiver<Output>>>,
    cleanup: Mutex<Option<Cleanup>>,
}

#[pymethods]
impl Subscription {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&self, py: Python<'_>) -> PyResult<(Node, PyAttributeValue)> {
        loop {
            py.check_signals()?;

            let message = py.detach(|| {
                util::blocking(|| {
                    let mut output = self.output.lock().map_err(|_| {
                        PyRuntimeError::new_err("subscription output lock poisoned")
                    })?;

                    let Some(receiver) = output.as_ref() else {
                        return Err(PyStopIteration::new_err(()));
                    };

                    let result = receiver.recv_timeout(Duration::from_millis(50));
                    let output_finished = matches!(
                        result,
                        Ok(Output::End(_)) | Err(mpsc::RecvTimeoutError::Disconnected)
                    );

                    if output_finished {
                        *output = None;
                    }

                    Ok(result)
                })
            })?;

            match message {
                Err(mpsc::RecvTimeoutError::Timeout) => (),

                Ok(Output::Sample(node, value)) => {
                    return Ok((node, PyAttributeValue::from_native(self.attribute, value)));
                }

                Ok(Output::End(error)) => {
                    return Err(error.unwrap_or_else(|| PyStopIteration::new_err(())));
                }

                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    return Err(PyRuntimeError::new_err(
                        "subscription producer ended unexpectedly",
                    ));
                }
            }
        }
    }

    /// Stop and join production, flush pending polls, and attempt remote deletion.
    ///
    /// Waits with the GIL released and without signal-driven cancellation. Repeated
    /// calls are harmless and do not wait for another caller's cleanup. Queued
    /// samples remain available; a pending terminal failure follows them.
    /// Native 0.19 restoration/deletion races limit remote cleanup during recovery.
    ///
    /// Raises:
    ///     OpcUaError: Native deletion fails. This does not add an iterator failure.
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        let cleanup = self
            .cleanup
            .lock()
            .map_err(|_| PyRuntimeError::new_err("subscription cleanup lock poisoned"))?
            .take();

        match cleanup {
            Some(cleanup) => util::wait(py, cleanup.close()),
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
    ) -> PyResult<()> {
        match (value, self.close(py)) {
            (Some(body), Err(cleanup)) => {
                PyErr::from_value(body.clone().into_any()).set_context(py, Some(cleanup));
                Ok(())
            }

            (_, result) => result,
        }
    }
}

#[pymethods]
impl OpcUaClient {
    /// Monitor one attribute of an ordered iterable of Nodes or Root-relative paths.
    ///
    /// The iterable is consumed once before native allocation. Every requested
    /// item must be established before setup succeeds. All items share settings.
    ///
    /// Args:
    ///     nodes (Iterable[Node | RelativePath | str]): Nonempty iterable. Nodes must
    ///         belong to this client; paths start at RootFolder. Resolved NodeIds
    ///         must be distinct, including across different input forms.
    ///     attribute (AttributeId | int | str | None): Attribute ID or canonical or
    ///         snake-case name. Defaults to ``Value``.
    ///     publishing_interval (Duration | datetime.timedelta | int | float):
    ///         Requested interval, with numeric values in seconds. Defaults to 0.5 seconds.
    ///     lifetime_count (int): Requested subscription lifetime in publishing cycles.
    ///         Defaults to ``60``.
    ///     max_keep_alive_count (int): Maximum cycles between keep-alives. Defaults to ``20``.
    ///     max_notifications_per_publish (int): Notification limit per publish;
    ///         zero means no limit. Defaults to ``0``.
    ///     priority (int): Subscription priority from 0 through 255. Defaults to ``0``.
    ///     publishing_enabled (bool): Enable native publishing. Defaults to ``True``.
    ///     sampling_interval (float): Finite native milliseconds; negative values
    ///         request the publishing interval, zero requests fastest sampling.
    ///         Fractional values are preserved. Defaults to ``0.0``.
    ///     queue_size (int): Requested native monitored-item queue size; zero lets
    ///         the server choose its minimum. Defaults to ``0``.
    ///     discard_oldest (bool): Discard the oldest queued native item on overflow.
    ///         Defaults to ``False``.
    ///     poll_interval (Duration | datetime.timedelta | int | float | None):
    ///         Positive quiet-Node poll interval, with numeric values in seconds.
    ///         Defaults to ``None``, disabling polling. Failed reads are logged
    ///         and the existing polling schedule continues.
    ///
    /// Returns:
    ///     Subscription: Iterator yielding ``(Node, AttributeValue)`` pairs.
    ///
    /// Raises:
    ///     ValueError: The group is empty, contains foreign or duplicate Nodes,
    ///         or a setting is invalid.
    ///     ExceptionGroup: Item setup fails; ordered errors retain ``node`` and
    ///         ``status_code``. Native rollback is attempted before reporting failure.
    ///     OpcUaError: A native service operation fails.
    ///
    /// Examples:
    ///     >>> with client.subscribe([client.objects.get_child("/Server/ServerStatus/CurrentTime")]) as samples:
    ///     ...     node, sample = next(samples)
    ///     ...     print(node.node_id, sample.value)
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature =  (
        nodes,
        *,
        attribute = None,
        publishing_interval = DurationInput::from_millis(500),
        lifetime_count = NativeInteger(60),
        max_keep_alive_count = NativeInteger(20),
        max_notifications_per_publish = NativeInteger(0),
        priority = NativeInteger(0),
        publishing_enabled = true,
        sampling_interval = 0.0,
        queue_size = NativeInteger(0),
        discard_oldest = false,
        poll_interval = None
    ))]
    fn subscribe(
        &self,
        py: Python<'_>,
        nodes: &Bound<'_, PyAny>,
        attribute: Option<&Bound<'_, PyAny>>,
        publishing_interval: DurationInput,
        lifetime_count: NativeInteger<u32>,
        max_keep_alive_count: NativeInteger<u32>,
        max_notifications_per_publish: NativeInteger<u32>,
        priority: NativeInteger<u8>,
        publishing_enabled: bool,
        sampling_interval: f64,
        queue_size: NativeInteger<u32>,
        discard_oldest: bool,
        poll_interval: Option<DurationInput>,
    ) -> PyResult<Subscription> {
        let attribute = attribute
            .map(node::attribute_id)
            .transpose()?
            .unwrap_or(ua::AttributeId::Value);

        let nodes = NodeInput::materialize(self, nodes)?;

        let settings = Settings {
            publishing_interval: publishing_interval.into_native(),
            lifetime_count: lifetime_count.0,
            max_keep_alive_count: max_keep_alive_count.0,
            max_notifications_per_publish: max_notifications_per_publish.0,
            priority: priority.0,
            publishing_enabled,
            sampling_interval,
            queue_size: queue_size.0,
            discard_oldest,
            poll_interval: poll_interval.map(DurationInput::into_native),
        };

        if !settings.sampling_interval.is_finite() {
            return Err(PyValueError::new_err(
                "sampling_interval must be finite milliseconds",
            ));
        }

        let invalid_poll_interval = settings.poll_interval.is_some_and(|period| {
            period.is_zero() || tokio::time::Instant::now().checked_add(period).is_none()
        });

        if invalid_poll_interval {
            return Err(PyValueError::new_err(
                "poll_interval must be positive and within the native timer range",
            ));
        }

        let runtime = crate::shared_rt::get(py)?.handle().clone();
        let client = self.clone();
        util::wait(py, create(client, nodes, attribute, settings, runtime))
    }
}

async fn create(
    client: OpcUaClient,
    inputs: Vec<NodeInput>,
    attribute: ua::AttributeId,
    settings: Settings,
    runtime: Handle,
) -> PyResult<Subscription> {
    let owner = client.clone();
    client
        .with_session(async move |session| {
            create_in(session, owner, inputs, attribute, settings, runtime).await
        })
        .await
}

async fn create_in(
    session: &Session,
    owner: OpcUaClient,
    inputs: Vec<NodeInput>,
    attribute: ua::AttributeId,
    settings: Settings,
    runtime: Handle,
) -> PyResult<Subscription> {
    let root = Node::root(owner.clone());
    let mut nodes = Vec::with_capacity(inputs.len());
    let mut ids = HashSet::new();

    for input in inputs {
        let node = match input {
            NodeInput::Node(node) => node,
            NodeInput::Path(path) => root.resolve_in(session, path).await?,
        };

        if !ids.insert(node.id().clone()) {
            return Err(PyValueError::new_err(
                "a Node may occur only once in a subscription group",
            ));
        }

        nodes.push(node);
    }

    let requests: Vec<_> = nodes
        .iter()
        .map(|node| {
            let item_to_monitor = ua::ReadValueId {
                node_id: node.id().clone(),
                attribute_id: attribute as u32,
                ..Default::default()
            };

            let requested_parameters = ua::MonitoringParameters {
                sampling_interval: settings.sampling_interval,
                queue_size: settings.queue_size,
                discard_oldest: settings.discard_oldest,
                ..Default::default()
            };

            ua::MonitoredItemCreateRequest {
                item_to_monitor,
                monitoring_mode: ua::MonitoringMode::Reporting,
                requested_parameters,
            }
        })
        .collect();

    let reads = requests
        .iter()
        .map(|item| item.item_to_monitor.clone())
        .collect();

    let (input, notifications) = async_mpsc::unbounded_channel();
    let status_input = input.clone();
    let callbacks = SubscriptionCallbacks::new(
        move |status| {
            let _ = status_input.send(Input::End(status.status));
        },
        move |value, item: &MonitoredItem| {
            let _ = input.send(Input::Sample(item.item_to_monitor().node_id.clone(), value));
        },
        |_, _| {},
    );

    let id = session
        .create_subscription(
            settings.publishing_interval,
            settings.lifetime_count,
            settings.max_keep_alive_count,
            settings.max_notifications_per_publish,
            settings.priority,
            settings.publishing_enabled,
            callbacks,
        )
        .await
        .map_err(error::native)?;

    let mut cleanup = Cleanup {
        client: owner.clone(),
        id: Some(id),
        worker: None,
        runtime: runtime.clone(),
    };

    let created = session
        .create_monitored_items(id, ua::TimestampsToReturn::Both, requests)
        .await;

    let setup = match created {
        Err(error) => Err(error::native(error)),
        Ok(created) if created.len() != nodes.len() => Err(error::status(
            ua::StatusCode::BadUnexpectedError,
            "monitored item creation returned an unexpected result count",
        )),
        Ok(created) => Python::attach(|py| {
            let mut rejected = Vec::new();

            for (node, item) in nodes.iter().zip(created) {
                if !item.result.status_code.is_good() {
                    let failure = error::status(item.result.status_code, "monitored item rejected");
                    failure.value(py).setattr("node", node.clone())?;
                    rejected.push(failure.into_value(py));
                }
            }

            if rejected.is_empty() {
                Ok(())
            } else {
                let group = py
                    .import("builtins")?
                    .getattr("ExceptionGroup")?
                    .call1(("subscription setup rejected", rejected))?;

                Err(PyErr::from_value(group))
            }
        }),
    };

    if let Err(failure) = setup {
        let deleted = delete(session, id).await;
        cleanup.id = None;

        if let Err(deleted) = deleted {
            Python::attach(|py| failure.set_context(py, Some(deleted)));
        }

        return Err(failure);
    }

    let (stop, stopped) = oneshot::channel();
    let (output, receiver) = mpsc::channel();
    let producer = Producer::new(
        nodes,
        reads,
        notifications,
        owner.native_state(),
        stopped,
        output,
        settings.poll_interval,
    );

    let poll_client = owner;
    let production = producer.run(move |reads| {
        let client = poll_client.clone();
        Box::pin(async move {
            client
                .with_session(async |session| {
                    session
                        .read(&reads, ua::TimestampsToReturn::Both, 0.0)
                        .await
                        .map_err(error::native)
                })
                .await
        })
    });

    let task = runtime.spawn(production);

    cleanup.worker = Some(Worker {
        stop: Some(stop),
        task,
    });

    Ok(Subscription {
        attribute,
        output: Mutex::new(Some(receiver)),
        cleanup: Mutex::new(Some(cleanup)),
    })
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<Subscription>()
}

#[cfg(test)]
mod tests;
