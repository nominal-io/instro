use std::collections::VecDeque;

use instro_opcua_test::TestServer;
use instro_opcua_test::server::FolderSpec;
use pyo3::exceptions::PyAssertionError;
use pyo3::types::PyDict;
use tokio::sync::watch;

use super::*;
use crate::client::ClientState;
use crate::test_support;
use crate::test_support::TestResult;
use crate::test_support::status;

fn fixture() -> TestResult<(TestServer, OpcUaClient, [Node; 2])> {
    let server = TestServer::builder().start()?;
    let client = test_support::client(&server)?;
    let nodes = test_support::with_python(|py| -> PyResult<_> {
        let object = Py::new(py, client.clone())?;
        let node = |path| -> PyResult<Node> {
            Ok(object
                .bind(py)
                .call_method1("get_node", (path,))?
                .extract()?)
        };

        Ok([
            node("/Objects/Server/ServerStatus/State")?,
            node("/Objects/Server/ServerStatus/CurrentTime")?,
        ])
    })?;

    Ok((server, client, nodes))
}

struct Production {
    producer: Producer,
    input: async_mpsc::UnboundedSender<Input>,
    state: watch::Sender<ClientState>,
    stop: oneshot::Sender<()>,
    output: mpsc::Receiver<Output>,
}

impl Production {
    fn new(nodes: Vec<Node>, attribute_id: u32, period: Option<Duration>) -> Self {
        let reads = nodes
            .iter()
            .map(|node| ua::ReadValueId {
                node_id: node.id().clone(),
                attribute_id,
                ..Default::default()
            })
            .collect();

        let (input, notifications) = async_mpsc::unbounded_channel();
        let (state, completion) = watch::channel(ClientState::Connected);
        let (stop, stopped) = oneshot::channel();
        let (output, receiver) = mpsc::channel();
        let producer = Producer::new(
            nodes,
            reads,
            notifications,
            completion,
            stopped,
            output,
            period,
        );

        Self {
            producer,
            input,
            state,
            stop,
            output: receiver,
        }
    }
}

fn quiet_subscription<'py>(
    py: Python<'py>,
    client: OpcUaClient,
    node: Node,
) -> PyResult<Bound<'py, PyAny>> {
    let globals = test_support::globals(py, client)?;
    globals.set_item("node", node)?;
    py.eval(
        c"client.subscribe([node], publishing_enabled=False)",
        Some(&globals),
        None,
    )
}

fn sample(py: Python<'_>, sub: &Subscription) -> PyResult<i32> {
    let (_, sample) = sub.__next__(py)?;
    Py::new(py, sample)?.bind(py).getattr("value")?.extract()
}

async fn subscriptions_empty(client: OpcUaClient) -> PyResult<bool> {
    client
        .with_session(async |session| {
            let subscriptions = session.subscription_state().lock();
            Ok(subscriptions
                .subscription_ids()
                .is_none_or(|ids| ids.is_empty()))
        })
        .await
}

fn native_id(sub: &Subscription) -> PyResult<u32> {
    sub.cleanup
        .lock()
        .map_err(|_| PyAssertionError::new_err("cleanup lock poisoned"))?
        .as_ref()
        .and_then(|owner| owner.id)
        .ok_or_else(|| PyAssertionError::new_err("native subscription missing"))
}

fn delete_remotely(py: Python<'_>, client: OpcUaClient, id: u32) -> PyResult<()> {
    util::wait(py, async move {
        client
            .with_session(async |session| delete(session, id).await)
            .await
    })
}

fn value(number: i32, source: Option<i64>, server: Option<i64>) -> ua::DataValue {
    ua::DataValue {
        value: Some(ua::Variant::Int32(number)),
        source_timestamp: source.map(ua::DateTime::from),
        server_timestamp: server.map(ua::DateTime::from),
        ..Default::default()
    }
}

fn numbers(receiver: &mpsc::Receiver<Output>) -> Vec<i32> {
    receiver
        .try_iter()
        .filter_map(|output| match output {
            Output::Sample(
                _,
                ua::DataValue {
                    value: Some(ua::Variant::Int32(number)),
                    ..
                },
            ) => Some(number),
            _ => None,
        })
        .collect()
}

#[tokio::test(start_paused = true)]
async fn production_merge_suppresses_equal_poll_and_preserves_either_older_timestamp() -> TestResult
{
    let (_server, client, nodes) = fixture()?;
    let [node, _] = nodes;
    let id = node.id().clone();
    let production = Production::new(vec![node], 13, Some(Duration::from_millis(10)));

    let fractional_poll = ua::DataValue {
        source_picoseconds: Some(1),
        ..value(40, Some(80), None)
    };

    let fractional_notification = ua::DataValue {
        source_picoseconds: Some(2),
        ..value(41, Some(80), None)
    };

    let cases = [
        (
            value(10, Some(10), Some(30)),
            value(11, Some(20), Some(20)),
            vec![10, 11],
        ),
        (
            value(20, Some(50), Some(40)),
            value(21, Some(40), Some(50)),
            vec![20, 21],
        ),
        (
            value(30, Some(70), None),
            value(31, Some(70), None),
            vec![31],
        ),
        (fractional_poll, fractional_notification, vec![40, 41]),
    ];

    let mut polls: VecDeque<_> = cases.iter().map(|(poll, _, _)| poll.clone()).collect();
    let future = production.producer.run(move |_| {
        let poll = polls
            .pop_front()
            .ok_or_else(|| PyAssertionError::new_err("unexpected poll"));
        Box::pin(async move { Ok(vec![poll?]) })
    });

    tokio::pin!(future);
    assert!(futures_util::poll!(&mut future).is_pending());

    for (_, notification, expected) in cases {
        tokio::time::advance(Duration::from_millis(10)).await;
        assert!(futures_util::poll!(&mut future).is_pending());
        assert!(production.output.try_recv().is_err());

        production
            .input
            .send(Input::Sample(id.clone(), notification))
            .map_err(|error| PyAssertionError::new_err(error.to_string()))?;

        assert!(futures_util::poll!(&mut future).is_pending());
        assert_eq!(numbers(&production.output), expected);
    }

    let _ = production.stop.send(());
    future.await;

    client.disconnect_async().await?;
    Ok(())
}

#[tokio::test(start_paused = true)]
async fn production_polling_tracks_processed_activity_continues_after_read_failure_and_flushes_on_stop()
-> TestResult {
    let (_server, client, nodes) = fixture()?;
    let [first, second] = &nodes;
    let first_id = first.id().clone();
    let second_id = second.id().clone();
    let production = Production::new(nodes.into(), 3, Some(Duration::from_millis(10)));

    let (requested, requests) = mpsc::channel();
    let mut call = 0;
    let future = production.producer.run(move |reads| {
        let requested_attributes = reads
            .iter()
            .map(|read| (read.node_id.clone(), read.attribute_id))
            .collect::<Vec<_>>();
        let _ = requested.send(requested_attributes);

        call += 1;
        let result = if call == 1 {
            Err(error::status(
                ua::StatusCode::BadCommunicationError,
                "injected service error",
            ))
        } else {
            Ok(reads.iter().map(|_| value(42, None, None)).collect())
        };

        Box::pin(async move { result })
    });

    tokio::pin!(future);
    assert!(futures_util::poll!(&mut future).is_pending());

    tokio::time::advance(Duration::from_millis(5)).await;
    production
        .input
        .send(Input::Sample(first_id.clone(), value(7, Some(-1000), None)))
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;

    assert!(futures_util::poll!(&mut future).is_pending());
    assert_eq!(numbers(&production.output), vec![7]);

    tokio::time::advance(Duration::from_millis(5)).await;
    assert!(futures_util::poll!(&mut future).is_pending());

    tokio::time::advance(Duration::from_millis(10)).await;
    assert!(futures_util::poll!(&mut future).is_pending());
    assert!(production.output.try_recv().is_err());

    assert_eq!(
        requests.try_iter().collect::<Vec<_>>(),
        vec![
            vec![(second_id.clone(), 3)],
            vec![(first_id, 3), (second_id, 3)]
        ]
    );

    let _ = production.stop.send(());
    future.await;

    assert_eq!(numbers(&production.output), vec![42, 42]);

    client.disconnect_async().await?;
    Ok(())
}

#[test]
fn setup_normalizes_once_rejects_aliases_and_rolls_back_ordered_item_errors() -> TestResult {
    let server = TestServer::builder()
        .add_folder(FolderSpec::new(1000, "Alpha"))
        .add_folder(FolderSpec::new(1001, "Beta"))
        .start()?;
    let client = test_support::client(&server)?;

    Python::attach(|py| -> PyResult<()> {
        let globals = test_support::globals(py, client.clone())?;
        globals.set_item("ns", server.namespace_index())?;

        py.run(
            c"
state_path = '/Objects/Server/ServerStatus/State'
state = client.get_node(state_path)
a = client.get_node(f'/Objects/{ns}:Alpha')
b = client.get_node(f'/Objects/{ns}:Beta')
try:
    client.subscribe(iter([a, state, b]))
except ExceptionGroup as group:
    assert [error.node.node_id.identifier for error in group.exceptions] == [1000, 1001]
    assert all(error.status_code.is_bad for error in group.exceptions)
else:
    raise AssertionError('item rejection did not fail setup')

class IterationFailure(Exception):
    pass

def broken():
    yield state
    raise IterationFailure('broken iterator')

for inputs, expected in [
    ([], ValueError),
    ([state, state_path], ValueError),
    (broken(), IterationFailure),
]:
    try:
        client.subscribe(inputs)
    except expected:
        pass
    else:
        raise AssertionError('invalid subscription input was admitted')
",
            Some(&globals),
            None,
        )?;

        assert!(util::wait(py, subscriptions_empty(client.clone()))?);

        util::wait(py, async move { client.disconnect_async().await })
    })?;
    Ok(())
}

#[tokio::test(start_paused = true)]
async fn terminal_paths_drain_admitted_samples_before_original_failure() -> TestResult {
    let (_server, client, nodes) = fixture()?;

    let [node, _] = &nodes;
    let id = node.id().clone();

    for native_completion in [false, true] {
        let production = Production::new(nodes.to_vec(), 13, None);

        let future = production
            .producer
            .run(|_| Box::pin(async { Err(PyAssertionError::new_err("unexpected poll")) }));
        tokio::pin!(future);
        production
            .input
            .send(Input::Sample(id.clone(), value(17, None, None)))?;

        assert!(futures_util::poll!(&mut future).is_pending());

        let expected = if native_completion {
            production.state.send_replace(ClientState::Finished(Ok(
                ua::StatusCode::BadConnectionClosed,
            )));
            0x80AE_0000
        } else {
            production
                .input
                .send(Input::End(ua::StatusCode::BadTimeout))?;

            let _ = production.stop.send(());
            0x800A_0000
        };

        future.await;
        let subscription = Subscription {
            attribute: ua::AttributeId::Value,
            output: Mutex::new(Some(production.output)),
            cleanup: Mutex::new(None),
        };

        Python::attach(|py| -> TestResult {
            assert_eq!(sample(py, &subscription)?, 17);

            let error = subscription
                .__next__(py)
                .err()
                .ok_or("terminal failure missing")?;
            assert_eq!(status(error)?, expected);

            assert!(
                subscription
                    .__next__(py)
                    .err()
                    .ok_or("iteration unexpectedly continued")?
                    .is_instance_of::<PyStopIteration>(py)
            );

            Ok(())
        })?;
    }

    client.disconnect_async().await?;
    Ok(())
}

#[test]
fn owned_close_retains_admitted_output_after_successful_and_failed_deletion() -> TestResult {
    let (_server, client, nodes) = fixture()?;

    let [node, _] = nodes;

    Python::attach(|py| -> TestResult {
        for delete_first in [false, true] {
            let subscription = quiet_subscription(py, client.clone(), node.clone())?;

            let sub = subscription
                .extract::<PyRef<'_, Subscription>>()
                .map_err(PyErr::from)?;
            let (admitted, receiver) = mpsc::channel();
            admitted.send(Output::Sample(node.clone(), value(91, None, None)))?;

            admitted.send(Output::End(None))?;

            *sub.output.lock().map_err(|_| "output lock poisoned")? = Some(receiver);

            if delete_first {
                delete_remotely(py, client.clone(), native_id(&sub)?)?;
            }

            let result = sub.close(py);
            if delete_first {
                let error = result.unwrap_err();
                assert_eq!(status(error)?, 0x80AB_0000);
            } else {
                result?;
            }

            assert_eq!(sample(py, &sub)?, 91);

            assert!(
                sub.__next__(py)
                    .err()
                    .ok_or("iteration unexpectedly continued")?
                    .is_instance_of::<PyStopIteration>(py)
            );

            sub.close(py)?;
        }

        util::wait(py, async move { client.disconnect_async().await })?;
        Ok(())
    })?;
    Ok(())
}

#[test]
fn context_exit_keeps_body_error_primary_when_native_deletion_fails() -> TestResult {
    let (_server, client, nodes) = fixture()?;

    let [node, _] = nodes;

    Python::attach(|py| {
        let subscription = quiet_subscription(py, client.clone(), node)?;
        let sub = subscription.extract::<PyRef<'_, Subscription>>()?;
        delete_remotely(py, client.clone(), native_id(&sub)?)?;
        let globals = PyDict::new(py);
        globals.set_item("sub", subscription)?;

        py.run(
            c"
body = ValueError('body failure')
try:
    with sub:
        raise body
except ValueError as error:
    assert error is body
    assert error.__context__.status_code.code == 0x80AB0000
else:
    raise AssertionError('body failure was hidden')
",
            Some(&globals),
            None,
        )?;

        util::wait(py, async move { client.disconnect_async().await })
    })?;
    Ok(())
}

#[test]
fn quiet_iterator_interruption_can_resume_without_an_orphan_receive() -> TestResult {
    if test_support::isolated(
        "subscription::tests::quiet_iterator_interruption_can_resume_without_an_orphan_receive",
    )? {
        return Ok(());
    }

    let (_server, client, [node, _]) = fixture()?;
    let (sender, receiver) = mpsc::channel();
    let subscription = Subscription {
        attribute: ua::AttributeId::Value,
        output: Mutex::new(Some(receiver)),
        cleanup: Mutex::new(None),
    };

    test_support::with_python(|py| -> PyResult<()> {
        let globals = test_support::globals(py, client.clone())?;
        globals.set_item("sub", Py::new(py, subscription)?)?;
        py.run(
            c"
import signal
import threading
import time

signal.signal(signal.SIGINT, signal.default_int_handler)
def interrupt():
    time.sleep(0.1)
    signal.raise_signal(signal.SIGINT)

thread = threading.Thread(target=interrupt)
thread.start()
try:
    next(sub)
except KeyboardInterrupt:
    pass
else:
    raise AssertionError('quiet iterator did not interrupt')
thread.join()
",
            Some(&globals),
            None,
        )?;

        sender
            .send(Output::Sample(node, value(91, None, None)))
            .map_err(|error| PyAssertionError::new_err(error.to_string()))?;

        sender
            .send(Output::End(None))
            .map_err(|error| PyAssertionError::new_err(error.to_string()))?;

        py.run(
            c"
assert next(sub)[1].value == 91
try:
    next(sub)
except StopIteration:
    pass
else:
    raise AssertionError('iteration continued after the sole sample')
",
            Some(&globals),
            None,
        )?;

        util::wait(py, async move { client.disconnect_async().await })
    })?;
    Ok(())
}

#[test]
fn iterator_and_disconnect_progress_on_shared_runtime_single_worker()
-> Result<(), Box<dyn std::error::Error>> {
    if test_support::isolated(
        "subscription::tests::iterator_and_disconnect_progress_on_shared_runtime_single_worker",
    )? {
        return Ok(());
    }

    let (_server, client, _nodes) = fixture()?;

    Python::attach(|py| -> PyResult<()> {
        let runtime = crate::shared_rt::get(py)?;
        let operation = runtime.spawn(async move {
            test_support::scenario(
                client,
                c"
with client.subscribe(
    ['/Objects/Server/ServerStatus/State'], publishing_enabled=False, poll_interval=0.01
) as sub:
    assert next(sub)[1].value == 0

with client.subscribe(
    ['/Objects/Server/ServerStatus/State'], attribute='BrowseName', publishing_interval=0.01
) as sub:
    sample = next(sub)[1]
    assert sample.attribute_id.value == 3 and sample.value.name == 'State'

client.disconnect()
",
            )
        });

        py.detach(|| pollster::block_on(operation))
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
    })?;

    Ok(())
}

#[test]
fn close_wakes_quiet_iterator_and_disconnect_ends_production() -> TestResult {
    let (_server, client, _nodes) = fixture()?;

    test_support::scenario(
        client,
        c"
import threading

state = client.get_node('/Objects/Server/ServerStatus/State')
sub = client.subscribe([state], publishing_enabled=False)
entered = threading.Event()
ended = threading.Event()

def consume():
    entered.set()
    try:
        next(sub)
    except StopIteration:
        ended.set()

thread = threading.Thread(target=consume)
thread.start()
assert entered.wait(2)

sub.close()
thread.join(2)
assert ended.is_set()

sub = client.subscribe([state], publishing_enabled=False)
client.disconnect()
assert list(sub) == []
",
    )?;
    Ok(())
}

#[test]
fn last_subscription_drop_initiates_native_deletion() -> TestResult {
    let (_server, client, [node, _]) = fixture()?;
    test_support::with_python(|py| -> PyResult<()> {
        drop(quiet_subscription(py, client.clone(), node)?);
        util::wait(py, async move {
            tokio::time::timeout(Duration::from_secs(3), async {
                while !subscriptions_empty(client.clone()).await? {
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }

                client.disconnect_async().await
            })
            .await
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
        })
    })?;
    Ok(())
}
