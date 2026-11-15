use std::collections::HashMap;
use std::collections::HashSet;
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use async_channel::Receiver;
use async_channel::Sender;
use futures_util::StreamExt;
use opcua::client::MonitoredItem;
use opcua::client::SubscriptionCallbacks;
use opcua::types as ua;
use pyo3::exceptions::PyBaseException;
use pyo3::exceptions::PyRuntimeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use tokio::task::JoinHandle;
use tokio::time::Instant;
use tokio::time::Interval;
use tokio::time::MissedTickBehavior;
use tokio::time::interval_at;
use tokio::time::timeout;

use crate::client::OpcUaClient;
use crate::config::DurationInput;
use crate::config::NativeInteger;
use crate::error;
use crate::node::Node;
use crate::node::NodeInput;
use crate::node::attribute_id;
use crate::util;
use crate::values::PyAttributeValue;

type Sample = (usize, ua::DataValue);

/// An iterator retaining its Nodes, client, and every accepted sample.
///
/// Create this with ``client.subscribe(nodes, ...)``. Native processing continues
/// while Python is idle. Optional fallback reads cover Nodes that did not notify
/// during the current interval, using the group's selected attribute.
///
/// Poll results wait until the next interval or a matching notification. A poll
/// strictly older on either comparable timestamp is delivered before that
/// notification; otherwise the notification replaces it. Unmatched polls survive.
///
/// Explicit ``close()`` seals notification admission, finishes any admitted read,
/// drains accepted notifications, flushes polls, and attempts native deletion.
/// Retained samples remain iterable afterward. A terminal native error follows
/// them once. Deletion errors belong to ``close()``, not iteration.
///
/// Examples:
///     >>> with client.subscribe([client.objects.get_child("2:Temperature")]) as samples:
///     ...     node, value = next(samples)
///     ...     print(node.node_id, value.value)
#[pyclass(frozen, module = "instro.unstable._opcua")]
pub(crate) struct Subscription {
    nodes: Arc<Vec<Node>>,
    attribute: ua::AttributeId,
    ingress: Sender<Result<Sample, ua::StatusCode>>,
    samples: Receiver<PyResult<Sample>>,
    task: Mutex<Option<JoinHandle<PyResult<()>>>>,
}

#[pymethods]
impl Subscription {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    /// Return the next retained Node and selected attribute value.
    ///
    /// Native waiting releases the GIL. A quiet wait checks Python signals every
    /// 50 milliseconds. Interruption cancels this wait alone, leaving subscription
    /// production and all unconsumed samples available for a later ``next()``.
    fn __next__(&self, py: Python<'_>) -> PyResult<Option<(Node, PyAttributeValue)>> {
        loop {
            py.check_signals()?;
            let samples = self.samples.clone();
            let received = util::wait(py, async move {
                Ok(timeout(Duration::from_millis(50), samples.recv()).await)
            })?;

            // No signal check follows removal: interruption must not discard a sample.
            match received {
                Err(_) => continue,
                Ok(Err(_)) => return Ok(None),

                Ok(Ok(value)) => {
                    let (index, value) = value?;
                    let node = self.nodes.get(index).ok_or_else(|| {
                        PyRuntimeError::new_err("notification referenced an unknown Node")
                    })?;

                    let value = PyAttributeValue::from_native(py, self.attribute, value)?;
                    return Ok(Some((node.clone(), value)));
                }
            }
        }
    }

    /// Stop production and wait for this caller's native cleanup to finish.
    ///
    /// The caller taking the task owns cleanup completion. Later callers return
    /// without a universal completion barrier. Waiting releases the GIL and is
    /// not cancelled midway by Python signals. A disconnected session needs no
    /// deletion; errors from an actually attempted deletion are reported.
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        self.ingress.close();
        let task = self
            .task
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .take();

        if let Some(task) = task {
            util::wait(py, async move {
                task.await
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
            })?;
        }

        Ok(())
    }

    /// Close on context exit, keeping a body exception primary.
    ///
    /// A cleanup failure is attached as the body's exception context. Without a
    /// body exception, the cleanup failure is raised directly.
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

impl Drop for Subscription {
    fn drop(&mut self) {
        // Dropping a Tokio JoinHandle detaches; the task retains the client until cleanup ends.
        self.ingress.close();
    }
}

#[pymethods]
impl OpcUaClient {
    /// Monitor a group of Nodes, with optional quiet-Node fallback reads.
    ///
    /// Args:
    ///     nodes (Iterable[Node | RelativePath | str]): Consumed once before native
    ///         allocation. Paths are rooted at RootFolder; duplicate or foreign
    ///         Nodes are rejected. Results retain these rooted Nodes.
    ///     attribute (AttributeId | int | str | None): Group attribute, default Value.
    ///     publishing_interval (Duration | timedelta | int | float): Seconds, default 0.5.
    ///     lifetime_count (int): Requested subscription lifetime count, default 60.
    ///     max_keep_alive_count (int): Requested keep-alive count, default 20.
    ///     max_notifications_per_publish (int): Zero means no requested limit.
    ///     priority (int): Native unsigned byte priority, default zero.
    ///     publishing_enabled (bool): Enable native publishing, default True.
    ///     sampling_interval (float): Finite native milliseconds; zero and negative
    ///         native sentinels and fractional milliseconds are passed through.
    ///     queue_size (int): Native monitored-item queue request, default zero.
    ///     discard_oldest (bool): Native monitored-item discard policy, default False.
    ///     poll_interval (Duration | timedelta | int | float | None): Strictly positive
    ///         fallback period in seconds, or None to disable fallback reads.
    ///
    /// Returns:
    ///     Subscription: Iterator over retained Node and AttributeValue pairs.
    ///
    /// Raises:
    ///     ValueError: A value, Node group, or resolved duplicate is invalid.
    ///     TypeError: A keyword or iterable element has an unsupported type.
    ///     ExceptionGroup: Monitored-item rejections in request order, each carrying
    ///         the Node identifier and original status. Setup is rolled back first.
    ///     OpcUaError: A native setup service fails. Cleanup cannot hide this error.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        nodes, *, attribute = None,
        publishing_interval = DurationInput::from_millis(500),
        lifetime_count = NativeInteger(60), max_keep_alive_count = NativeInteger(20),
        max_notifications_per_publish = NativeInteger(0), priority = NativeInteger(0),
        publishing_enabled = true, sampling_interval = 0.0,
        queue_size = NativeInteger(0), discard_oldest = false, poll_interval = None,
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
        let inputs = NodeInput::materialize(self, nodes)?;
        let attribute = attribute
            .map(attribute_id)
            .transpose()?
            .unwrap_or(ua::AttributeId::Value);
        let poll_interval = poll_interval.map(DurationInput::into_native);

        let invalid_poll = poll_interval.is_some_and(|period| period.is_zero());

        if !sampling_interval.is_finite() || invalid_poll {
            return Err(PyValueError::new_err(
                "sampling must be finite and polling strictly positive",
            ));
        }

        let client = self.clone();
        util::wait(py, async move {
            client
                .with_session(async |session| {
                    let root = Node::root(client.clone());
                    let mut nodes = Vec::with_capacity(inputs.len());
                    let mut unique = HashSet::new();

                    for input in inputs {
                        let node = match input {
                            NodeInput::Node(node) => node,
                            NodeInput::Path(path) => root.resolve_in(session, path).await?,
                        };

                        if !unique.insert(node.id().clone()) {
                            return Err(PyValueError::new_err(format!(
                                "duplicate Node {}",
                                node.id()
                            )));
                        }

                        nodes.push(node);
                    }

                    let (ingress, notifications) = async_channel::unbounded();
                    let callback_ingress = ingress.clone();
                    let status_ingress = ingress.clone();

                    let on_status_change = move |notification: ua::StatusChangeNotification| {
                        if notification.status.is_bad() {
                            let _ = status_ingress.try_send(Err(notification.status));
                        }
                    };

                    let on_data_value = move |value, item: &MonitoredItem| {
                        let index = item.client_handle() as usize - 1;
                        let _ = callback_ingress.try_send(Ok((index, value)));
                    };

                    let callback = SubscriptionCallbacks::new(
                        on_status_change,
                        on_data_value,
                        |_event, _item| {},
                    );

                    let id = session
                        .create_subscription(
                            publishing_interval.into_native(),
                            lifetime_count.0,
                            max_keep_alive_count.0,
                            max_notifications_per_publish.0,
                            priority.0,
                            publishing_enabled,
                            callback,
                        )
                        .await
                        .map_err(error::native)?;

                    let setup = async {
                        let mut requests = Vec::with_capacity(nodes.len());

                        for (index, node) in nodes.iter().enumerate() {
                            let handle = u32::try_from(index + 1)
                                .map_err(|_| PyValueError::new_err("too many monitored Nodes"))?;

                            let parameters = ua::MonitoringParameters {
                                client_handle: handle,
                                sampling_interval,
                                queue_size: queue_size.0,
                                discard_oldest,
                                ..Default::default()
                            };

                            let request = ua::MonitoredItemCreateRequest {
                                item_to_monitor: ua::ReadValueId::new(node.id().clone(), attribute),
                                monitoring_mode: ua::MonitoringMode::Reporting,
                                requested_parameters: parameters,
                            };

                            requests.push(request);
                        }

                        let results = session
                            .create_monitored_items(id, ua::TimestampsToReturn::Both, requests)
                            .await
                            .map_err(error::native)?;

                        if results.len() != nodes.len() {
                            return Err(error::status(
                                ua::StatusCode::BadUnexpectedError,
                                "monitored-item result count does not match the requested Nodes",
                            ));
                        }

                        Python::attach(|py| {
                            let mut errors = Vec::new();

                            for (node, item) in nodes.iter().zip(results) {
                                let status = item.result.status_code;

                                if !status.is_good() {
                                    let failure = error::status(
                                        status,
                                        format!("monitoring Node {}: {status}", node.id()),
                                    );

                                    failure.value(py).setattr("node", node.clone())?;
                                    errors.push(failure.into_value(py));
                                }
                            }

                            if errors.is_empty() {
                                return Ok(());
                            }

                            let group = py
                                .import("builtins")?
                                .getattr("ExceptionGroup")?
                                .call1(("monitored-item setup failed", errors))?;

                            Err(PyErr::from_value(group))
                        })?;

                        poll_interval
                            .map(|period| {
                                let deadline =
                                    Instant::now().checked_add(period).ok_or_else(|| {
                                        PyValueError::new_err(
                                            "poll interval exceeds the native clock range",
                                        )
                                    })?;

                                let mut timer = interval_at(deadline, period);
                                timer.set_missed_tick_behavior(MissedTickBehavior::Skip);

                                Ok(timer)
                            })
                            .transpose()
                    }
                    .await;

                    let timer = match setup {
                        Ok(timer) => timer,

                        Err(error) => {
                            ingress.close();
                            let _ = session.delete_subscription(id).await;
                            return Err(error);
                        }
                    };

                    let nodes = Arc::new(nodes);
                    let (output, samples) = async_channel::unbounded();

                    let task = tokio::spawn(subscription_loop(
                        client.clone(),
                        nodes.clone(),
                        attribute,
                        id,
                        notifications,
                        output,
                        timer,
                    ));

                    Ok(Subscription {
                        nodes,
                        attribute,
                        ingress,
                        samples,
                        task: Mutex::new(Some(task)),
                    })
                })
                .await
        })
    }
}

/// Run the original quiet-membership and one-interval reconciliation policy.
///
/// The only policy collections are the quiet indices and one pending poll per
/// Node. Native reads stay inline: notifications accepted during a read wait for
/// it to finish, and a Python interruption cannot abandon its Session guard.
/// Channel closure disables future reads and drains through the same merge path.
async fn subscription_loop(
    client: OpcUaClient,
    nodes: Arc<Vec<Node>>,
    attribute: ua::AttributeId,
    id: u32,
    notifications: Receiver<Result<Sample, ua::StatusCode>>,
    output: Sender<PyResult<Sample>>,
    mut timer: Option<Interval>,
) -> PyResult<()> {
    let mut quiet = (0..nodes.len()).collect::<HashSet<_>>();
    let mut pending = HashMap::<usize, ua::DataValue>::new();
    let mut terminal = None;
    let mut state = client.native_state();

    let chunks = notifications.clone().ready_chunks(64);
    tokio::pin!(chunks);

    loop {
        let tick = async {
            match timer.as_mut() {
                None => std::future::pending().await,

                Some(timer) => {
                    timer.tick().await;
                }
            }
        };

        tokio::select! {
            biased;
            _ = tick, if !notifications.is_closed() => {
                for sample in pending.drain() {
                    let _ = output.try_send(Ok(sample));
                }

                if !quiet.is_empty() {
                    let indices = quiet.iter().copied().collect::<Vec<_>>();
                    let requests = indices
                        .iter()
                        .filter_map(|index| nodes.get(*index))
                        .map(|node| ua::ReadValueId::new(node.id().clone(), attribute))
                        .collect::<Vec<_>>();

                    let read = client
                        .with_session(async |session| {
                            // This is the read's admission point, under the Session read guard.
                            // A close before admission skips it; an admitted read always completes.
                            if notifications.is_closed() {
                                return Ok(None);
                            }

                            let read = session
                                .read(&requests, ua::TimestampsToReturn::Both, 0.0)
                                .await;

                            Ok(Some(read))
                        })
                        .await;

                    match read {
                        Ok(None) => continue,

                        Err(_) => {
                            notifications.close();
                            continue;
                        }

                        Ok(Some(Ok(values))) if values.len() == indices.len() => {
                            pending.extend(indices.into_iter().zip(values));
                        }

                        other => {
                            tracing::warn!(result = ?other, "fallback subscription read failed");
                            continue;
                        }
                    }
                }

                quiet = (0..nodes.len()).collect();
            }

            changed = state.changed(), if !notifications.is_closed() => {
                let finished = state.borrow_and_update().finished_result();

                if changed.is_err() || finished.is_some() {
                    terminal = finished.and_then(Result::err);
                    notifications.close();
                }
            }

            chunk = chunks.next() => {
                let Some(chunk) = chunk else {
                    break;
                };

                for notification in chunk {
                    let (index, value) = match notification {
                        Ok(sample) => sample,

                        Err(status) => {
                            terminal.get_or_insert_with(|| {
                                error::status(status, "native subscription ended")
                            });

                            notifications.close();
                            continue;
                        }
                    };

                    quiet.remove(&index);
                    if let Some(polled) = pending.remove(&index)
                        && polled_is_older(&polled, &value)
                    {
                        let _ = output.try_send(Ok((index, polled)));
                    }

                    let _ = output.try_send(Ok((index, value)));
                }
            }
        }
    }

    for sample in pending {
        let _ = output.try_send(Ok(sample));
    }

    let terminal = terminal.or_else(|| state.borrow().finished_result().and_then(Result::err));
    if let Some(error) = terminal {
        let _ = output.try_send(Err(error));
    }

    drop(output);

    // The outer Result describes admission only; the inner Result is actual deletion.
    // A missing or terminal Session admits no deletion. Never suppress an attempted one.
    let deletion = client
        .with_session(async |session| {
            Ok(session.delete_subscription(id).await.map_err(error::native))
        })
        .await;

    match deletion {
        Err(_) => Ok(()),
        Ok(Err(error)) => Err(error),
        Ok(Ok(status)) if !status.is_good() => Err(error::status(status, "deleting subscription")),
        Ok(Ok(_)) => Ok(()),
    }
}

fn polled_is_older(polled: &ua::DataValue, notification: &ua::DataValue) -> bool {
    let source = polled.source_timestamp.zip(notification.source_timestamp);
    let server = polled.server_timestamp.zip(notification.server_timestamp);

    source.is_some_and(|(poll, note)| {
        (poll, polled.source_picoseconds.unwrap_or(0))
            < (note, notification.source_picoseconds.unwrap_or(0))
    }) || server.is_some_and(|(poll, note)| {
        (poll, polled.server_picoseconds.unwrap_or(0))
            < (note, notification.server_picoseconds.unwrap_or(0))
    })
}
