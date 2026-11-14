use std::time::Duration;

use instro_opcua_test::TestServer;
use pyo3::prelude::*;

pub(crate) type TestResult<T = ()> = Result<T, Box<dyn std::error::Error>>;

pub(crate) fn with_python<T>(
    operation: impl for<'py> FnOnce(Python<'py>) -> PyResult<T>,
) -> PyResult<T> {
    Python::initialize();
    Python::attach(operation)
}

pub(crate) fn module(py: Python<'_>) -> PyResult<Bound<'_, PyModule>> {
    let module = PyModule::new(py, "_opcua")?;
    crate::_opcua(&module)?;
    Ok(module)
}

pub(crate) fn connected<'py>(py: Python<'py>, server: &TestServer) -> PyResult<Bound<'py, PyAny>> {
    module(py)?
        .getattr("OpcUaClient")?
        .call_method1("connect", (server.endpoint_url(),))
}

pub(crate) fn client(server: &TestServer) -> PyResult<crate::client::OpcUaClient> {
    with_python(|py| Ok(connected(py, server)?.extract()?))
}

pub(crate) fn globals<'py>(
    py: Python<'py>,
    client: crate::client::OpcUaClient,
) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
    let globals = module(py)?.dict();
    globals.set_item("client", client)?;
    Ok(globals)
}

pub(crate) fn scenario(
    client: crate::client::OpcUaClient,
    program: &std::ffi::CStr,
) -> PyResult<()> {
    with_python(|py| py.run(program, Some(&globals(py, client)?), None))
}

pub(crate) fn status(error: PyErr) -> PyResult<u32> {
    with_python(|py| {
        assert!(error.is_instance_of::<crate::error::OpcUaError>(py));
        error
            .value(py)
            .getattr("status_code")?
            .getattr("code")?
            .extract()
    })
}

pub(crate) fn isolated(name: &str) -> Result<bool, Box<dyn std::error::Error>> {
    use std::process::Command;
    use std::process::Stdio;
    use std::time::Instant;

    const CHILD: &str = "INSTRO_OPCUA_SIGNAL_TEST_CHILD";

    if std::env::var(CHILD).is_ok_and(|child| child == name) {
        return Ok(false);
    }

    let mut child = Command::new(std::env::current_exe()?)
        .args(["--exact", name, "--nocapture"])
        .env(CHILD, name)
        .env("TOKIO_WORKER_THREADS", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let deadline = Instant::now() + Duration::from_secs(20);

    while child.try_wait()?.is_none() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }

    if child.try_wait()?.is_none() {
        child.kill()?;
    }

    let output = child.wait_with_output()?;

    assert!(
        output.status.success(),
        "isolated Python test subprocess failed:\n{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    Ok(true)
}
