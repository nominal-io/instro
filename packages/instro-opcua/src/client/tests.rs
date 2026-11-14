use std::time::Duration;

use futures_util::FutureExt;
use instro_opcua_test::TestServer;
use pyo3::types::PyDict;

use super::*;
use crate::test_support;
use crate::test_support::TestResult;
use crate::test_support::status;

async fn read_server_state(session: &Session) -> PyResult<Vec<ua::DataValue>> {
    let request = ua::ReadValueId {
        node_id: ua::VariableId::Server_ServerStatus_State.into(),
        attribute_id: ua::AttributeId::Value as u32,
        ..Default::default()
    };

    session
        .read(&[request], ua::TimestampsToReturn::Both, 0.0)
        .await
        .map_err(error::native)
}

#[tokio::test]
async fn native_read_drains_before_disconnect_and_later_read_is_rejected() -> TestResult {
    let server = TestServer::builder().start()?;

    let retained = test_support::client(&server)?;

    let read = retained.with_session(read_server_state);
    tokio::pin!(read);
    assert!(futures_util::poll!(&mut read).is_pending());

    let disconnect = retained.disconnect_async();
    tokio::pin!(disconnect);
    assert!(futures_util::poll!(&mut disconnect).is_pending());

    let later_read = retained.with_session(read_server_state);
    tokio::pin!(later_read);
    assert!(futures_util::poll!(&mut later_read).is_pending());

    read.await?;

    assert!(futures_util::poll!(&mut disconnect).is_pending());

    assert_eq!(status(later_read.await.unwrap_err())?, 0x800D_0000);

    assert!(matches!(
        retained.disconnect_async().now_or_never(),
        Some(Ok(()))
    ));

    disconnect.await?;
    Ok(())
}

#[tokio::test]
async fn recovery_disconnect_joins_native_termination() -> TestResult {
    let server = TestServer::builder().start()?;

    let client = test_support::client(&server)?;
    let mut state = client.native_state();

    drop(server);
    while !matches!(*state.borrow_and_update(), ClientState::Recovering) {
        state.changed().await?;
    }

    tokio::time::timeout(Duration::from_secs(5), client.disconnect_async()).await??;

    assert!(matches!(
        *state.borrow(),
        ClientState::Finished(Ok(ua::StatusCode::Good))
    ));

    Ok(())
}

#[test]
fn python_signals_abandon_setup_and_received_live_client() -> TestResult {
    if crate::test_support::isolated(
        "client::tests::python_signals_abandon_setup_and_received_live_client",
    )? {
        return Ok(());
    }

    test_support::with_python(|py| {
        let module = test_support::module(py)?;
        py.run(
            c"
import signal
import socket
import threading

signal.signal(signal.SIGINT, signal.default_int_handler)
listener = socket.socket()
listener.bind(('127.0.0.1', 0))
listener.listen()
endpoint = 'opc.tcp://127.0.0.1:%d' % listener.getsockname()[1]
closed = threading.Event()

def peer():
    for _ in range(2):
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(5)
            with connection.makefile('rb') as wire:
                assert wire.read(3) == b'HEL'
                signal.raise_signal(signal.SIGINT)
                while wire.read(1024):
                    pass
        closed.set()

thread = threading.Thread(target=peer, daemon=True)
thread.start()
for wait in [get_endpoints, OpcUaClient.connect]:
    closed.clear()
    try:
        wait(endpoint)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError('setup wait ignored signal')
    assert closed.wait(5)
thread.join(5)
assert not thread.is_alive()
listener.close()
",
            Some(&module.dict()),
            None,
        )
    })?;

    let server = TestServer::builder().start()?;

    Python::attach(|py| -> TestResult {
        let client = test_support::client(&server)?;
        let mut completion = client.native_state();

        let (sender, receiver) = mpsc::sync_channel(1);
        let delivery = std::thread::spawn(move || {
            Python::attach(|py| -> PyResult<()> {
                let signal = py.import("signal")?;
                signal.call_method1("raise_signal", (signal.getattr("SIGINT")?,))?;
                sender
                    .send(client)
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;

                Ok(())
            })
        });

        let failure = util::receive(py, receiver)
            .err()
            .ok_or("live client was delivered")?;

        assert!(failure.is_instance_of::<pyo3::exceptions::PyKeyboardInterrupt>(py));

        delivery.join().map_err(|_| "delivery thread panicked")??;

        util::wait(py, async move {
            tokio::time::timeout(Duration::from_secs(5), wait_for_completion(&mut completion))
                .await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))??;

            Ok(())
        })?;

        Ok(())
    })?;

    Ok(())
}

#[test]
fn connection_rejects_unreachable_native_timers_and_only_reachable_retry_overflow() -> PyResult<()>
{
    crate::test_support::with_python(|py| {
        let module = crate::test_support::module(py)?;
        let connect = module.getattr("OpcUaClient")?.getattr("connect")?;
        let cases = [
            ("keep_alive_interval", 0, true),
            ("keep_alive_interval", u64::MAX, true),
            ("min_publish_interval", u64::MAX, true),
            ("session_retry_initial", u64::MAX, true),
            ("session_retry_max", u64::MAX, false),
        ];

        for (setting, value, rejected) in cases {
            let kwargs = PyDict::new(py);
            kwargs.set_item(setting, value)?;
            let error = connect
                .call(("invalid endpoint",), Some(&kwargs))
                .unwrap_err();

            if rejected {
                assert!(error.is_instance_of::<PyValueError>(py), "{setting}");
            } else {
                assert!(error.is_instance_of::<error::OpcUaError>(py), "{setting}");
            }
        }

        let kwargs = PyDict::new(py);
        kwargs.set_item("session_retry_initial", u64::MAX)?;
        kwargs.set_item("session_retry_limit", 0)?;
        let error = connect
            .call(("invalid endpoint",), Some(&kwargs))
            .unwrap_err();

        assert!(error.is_instance_of::<error::OpcUaError>(py));
        Ok(())
    })
}
