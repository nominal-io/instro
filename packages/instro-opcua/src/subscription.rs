use std::collections::HashMap;
use std::collections::HashSet;
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use async_channel::Receiver;
use async_channel::Sender;
use futures_util::FutureExt;
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

use crate::client::OpcUaClient;
use crate::config::DurationInput;
use crate::config::extract_integer;
use crate::error;
use crate::node::Node;
use crate::node::NodeInput;
use crate::node::attribute_id;
use crate::util;
use crate::values::PyAttributeValue;

pyo3::import_exception!(builtins, ExceptionGroup);

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
        let received = util::wait_cancellable(py, async { Ok(self.samples.recv().await) })?;

        let sample = match received.ok() {
            None => return Ok(None),
            Some(value) => value?,
        };

        let (index, value) = sample;
        let node = self.nodes.get(index).ok_or(error::status(
            ua::StatusCode::BadUnknownResponse,
            "notification referenced an unknown Node",
        ))?;

        let value = PyAttributeValue::from_native(py, self.attribute, value);
        Ok(Some((node.clone(), value)))
    }

    /// Stop production and wait for this caller's native cleanup to finish.
    ///
    /// The caller taking the task owns cleanup completion. Later callers return
    /// without a universal completion barrier. Waiting releases the GIL and is
    /// not cancelled midway by Python signals. A disconnected session needs no
    /// deletion. Cleanup also succeeds if the server reports the subscription
    /// is already absent. Other deletion failures raise ``OpcUaError``.
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        util::wait(py, async {
            self.ingress.close();
            let task = self
                .task
                .lock()
                .unwrap_or_else(|error| error.into_inner())
                .take();

            let Some(task) = task else {
                return Ok(());
            };

            task.await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
        })
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
    /// If Python interrupts setup after allocation starts, native setup runs to
    /// completion and any resulting subscription is closed in the background.
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
    ///     OverflowError: An integer is outside the range of its subscription setting.
    ///     ExceptionGroup: Monitored-item rejections in request order, each carrying
    ///         the Node identifier and original status. Setup is rolled back first.
    ///     OpcUaError: A native setup service fails. Cleanup cannot hide this error.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(name = "subscribe", signature = (
        nodes, *, attribute = None,
        publishing_interval = DurationInput::from_millis(500),
        lifetime_count = 60, max_keep_alive_count = 20,
        max_notifications_per_publish = 0, priority = 0,
        publishing_enabled = true, sampling_interval = 0.0,
        queue_size = 0, discard_oldest = false, poll_interval = None,
    ))]
    fn subscribe_py(
        &self,
        py: Python<'_>,
        nodes: &Bound<'_, PyAny>,
        attribute: Option<&Bound<'_, PyAny>>,
        publishing_interval: DurationInput,
        #[pyo3(from_py_with = extract_integer)] lifetime_count: u32,
        #[pyo3(from_py_with = extract_integer)] max_keep_alive_count: u32,
        #[pyo3(from_py_with = extract_integer)] max_notifications_per_publish: u32,
        #[pyo3(from_py_with = extract_integer)] priority: u8,
        publishing_enabled: bool,
        sampling_interval: f64,
        #[pyo3(from_py_with = extract_integer)] queue_size: u32,
        discard_oldest: bool,
        poll_interval: Option<DurationInput>,
    ) -> PyResult<Subscription> {
        let inputs = NodeInput::materialize(self, nodes)?;
        if inputs.is_empty() {
            return Err(PyValueError::new_err(
                "subscription requires at least one Node",
            ));
        }

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

        let paths = inputs
            .iter()
            .filter_map(|input| match input {
                NodeInput::Node(_) => None,
                NodeInput::Path(path) => Some(path.clone()),
            })
            .collect::<Vec<_>>();

        let mut translated = if paths.is_empty() {
            Vec::new()
        } else {
            util::wait_cancellable(py, self.translate(paths))?
        }
        .into_iter();

        let mut nodes = Vec::with_capacity(inputs.len());
        let mut requests = Vec::with_capacity(inputs.len());
        let mut unique = HashSet::new();

        for (index, input) in inputs.into_iter().enumerate() {
            let node = match input {
                NodeInput::Node(node) => node,
                NodeInput::Path(_) => translated.next().ok_or_else(|| {
                    error::status(
                        ua::StatusCode::BadUnexpectedError,
                        "missing translated Node",
                    )
                })?,
            };

            let node_id = node.node_id();

            if !unique.insert(node_id.clone()) {
                return Err(PyValueError::new_err(format!(
                    "duplicate node: '{node_id}'"
                )));
            }

            let handle = u32::try_from(index + 1)
                .map_err(|_| PyValueError::new_err("too many monitored Nodes"))?;

            let parameters = ua::MonitoringParameters {
                client_handle: handle,
                sampling_interval,
                queue_size,
                discard_oldest,
                ..Default::default()
            };

            requests.push(ua::MonitoredItemCreateRequest {
                item_to_monitor: ua::ReadValueId::new(node.node_id().clone(), attribute),
                monitoring_mode: ua::MonitoringMode::Reporting,
                requested_parameters: parameters,
            });

            nodes.push(node);
        }

        poll_interval.map(poll_deadline).transpose()?;

        let options = SubscriptionOptions {
            attribute,
            publishing_interval: publishing_interval.into_native(),
            lifetime_count,
            max_keep_alive_count,
            max_notifications_per_publish,
            priority,
            publishing_enabled,
            poll_interval,
        };

        let client = self.clone();
        let outcome = util::wait_detached(py, async move {
            client.subscribe(nodes, requests, options).await
        })?;

        match outcome {
            Ok(subscription) => Ok(subscription),
            Err(rejections) => Err(monitored_item_exception_group(py, rejections)?),
        }
    }
}

struct SubscriptionOptions {
    attribute: ua::AttributeId,
    publishing_interval: Duration,
    lifetime_count: u32,
    max_keep_alive_count: u32,
    max_notifications_per_publish: u32,
    priority: u8,
    publishing_enabled: bool,
    poll_interval: Option<Duration>,
}

impl OpcUaClient {
    async fn subscribe(
        &self,
        nodes: Vec<Node>,
        requests: Vec<ua::MonitoredItemCreateRequest>,
        options: SubscriptionOptions,
    ) -> PyResult<Result<Subscription, Vec<(Node, ua::StatusCode)>>> {
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

        let callback =
            SubscriptionCallbacks::new(on_status_change, on_data_value, |_event, _item| {});

        self.with_session(async |session| {
            let id = session
                .create_subscription(
                    options.publishing_interval,
                    options.lifetime_count,
                    options.max_keep_alive_count,
                    options.max_notifications_per_publish,
                    options.priority,
                    options.publishing_enabled,
                    callback,
                )
                .await
                .map_err(error::native)?;

            let monitored_items = session
                .create_monitored_items(id, ua::TimestampsToReturn::Both, requests)
                .await
                .map_err(error::native);

            let setup = monitored_items.and_then(|results| {
                if results.len() != nodes.len() {
                    return Err(error::status(
                        ua::StatusCode::BadUnexpectedError,
                        "monitored-item result count does not match the requested Nodes",
                    ));
                }

                let rejections = nodes
                    .iter()
                    .zip(results)
                    .filter_map(|(node, item)| {
                        let status = item.result.status_code;
                        (!status.is_good()).then(|| (node.clone(), status))
                    })
                    .collect::<Vec<_>>();

                if !rejections.is_empty() {
                    return Ok(Err(rejections));
                }

                let timer = options
                    .poll_interval
                    .map(|period| {
                        poll_deadline(period).map(|deadline| {
                            let mut timer = interval_at(deadline, period);
                            timer.set_missed_tick_behavior(MissedTickBehavior::Skip);
                            timer
                        })
                    })
                    .transpose()?;

                Ok(Ok(timer))
            });

            if !matches!(&setup, Ok(Ok(_))) {
                ingress.close();
                let _ = session.delete_subscription(id).await;
            }

            let timer = match setup? {
                Ok(timer) => timer,
                Err(rejections) => return Ok(Err(rejections)),
            };

            let nodes = Arc::new(nodes);
            let (output, samples) = async_channel::unbounded();

            let task = tokio::spawn(subscription_loop(
                self.clone(),
                nodes.clone(),
                options.attribute,
                id,
                notifications,
                output,
                timer,
            ));

            Ok(Ok(Subscription {
                nodes,
                attribute: options.attribute,
                ingress,
                samples,
                task: Mutex::new(Some(task)),
            }))
        })
        .await
    }
}

fn poll_deadline(period: Duration) -> PyResult<Instant> {
    Instant::now()
        .checked_add(period)
        .ok_or_else(|| PyValueError::new_err("poll interval exceeds the native clock range"))
}

fn monitored_item_exception_group(
    py: Python<'_>,
    rejections: Vec<(Node, ua::StatusCode)>,
) -> PyResult<PyErr> {
    let mut errors = Vec::with_capacity(rejections.len());

    for (node, status) in rejections {
        let failure = error::status(
            status,
            format!("monitoring Node {}: {status}", node.node_id()),
        );

        failure.value(py).setattr("node", node)?;
        errors.push(failure.into_value(py));
    }

    Ok(ExceptionGroup::new_err((
        "monitored-item setup failed",
        errors,
    )))
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

        let (poll_due, chunk) = tokio::select! {
            biased;
            changed = state.changed(), if !notifications.is_closed() => {
                let finished = state.borrow_and_update().finished_result();

                if changed.is_err() || finished.is_some() {
                    terminal = finished.and_then(Result::err);
                    notifications.close();
                }
                continue;
            }

            _ = tick, if !notifications.is_closed() => {
                for sample in pending.drain() {
                    let _ = output.try_send(Ok(sample));
                }

                // Give queued notifications a turn before starting another read.
                (true, chunks.next().now_or_never().flatten().unwrap_or_default())
            }

            chunk = chunks.next() => {
                let Some(chunk) = chunk else {
                    break;
                };
                (false, chunk)
            }
        };

        for notification in chunk {
            let (index, value) = match notification {
                Ok(sample) => sample,

                Err(status) => {
                    terminal
                        .get_or_insert_with(|| error::status(status, "native subscription ended"));

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

        if !poll_due || notifications.is_closed() {
            continue;
        }

        if !quiet.is_empty() {
            let indices = quiet.iter().copied().collect::<Vec<_>>();

            let requests = indices
                .iter()
                .filter_map(|index| nodes.get(*index))
                .map(|node| ua::ReadValueId::new(node.node_id().clone(), attribute))
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
                }
            }
        }

        quiet = (0..nodes.len()).collect();
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
    // A missing or terminal Session admits no deletion.
    let deletion = client
        .with_session(async |session| {
            Ok(session.delete_subscription(id).await.map_err(error::native))
        })
        .await;

    match deletion {
        Err(_) | Ok(Ok(ua::StatusCode::BadSubscriptionIdInvalid)) => Ok(()),
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

#[cfg(test)]
mod polling_freshness_tests {
    use super::*;

    #[test]
    fn either_comparable_clock_can_make_poll_strictly_older() {
        let timestamp = ua::DateTime::from(116_444_736_000_000_000);
        let sample = |source: Option<u16>, server: Option<u16>| ua::DataValue {
            source_timestamp: source.map(|_| timestamp),
            source_picoseconds: source,
            server_timestamp: server.map(|_| timestamp),
            server_picoseconds: server,
            ..Default::default()
        };

        let later_tick = ua::DataValue {
            source_timestamp: Some(ua::DateTime::from(116_444_736_000_000_001)),
            source_picoseconds: Some(0),
            ..Default::default()
        };

        let missing_fraction = ua::DataValue {
            source_timestamp: Some(timestamp),
            ..Default::default()
        };

        let cases = [
            (
                sample(Some(101), Some(900)),
                sample(Some(102), Some(800)),
                true,
            ),
            (
                sample(Some(900), Some(101)),
                sample(Some(800), Some(102)),
                true,
            ),
            (
                sample(Some(102), Some(102)),
                sample(Some(101), Some(101)),
                false,
            ),
            (sample(Some(101), None), sample(Some(101), None), false),
            (sample(None, Some(101)), sample(Some(102), None), false),
            (sample(Some(0), None), sample(None, None), false),
            (sample(Some(9000), None), later_tick, true),
            (missing_fraction, sample(Some(0), None), false),
        ];

        for (polled, notification, expected) in cases {
            assert_eq!(polled_is_older(&polled, &notification), expected);
        }
    }
}
