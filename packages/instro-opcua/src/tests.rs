use std::ffi::CStr;
use std::sync::Arc;

use instro_opcua_test::Access;
use instro_opcua_test::FolderSpec;
use instro_opcua_test::ParentRef;
use instro_opcua_test::TestServer;
use instro_opcua_test::TestServerBuilder;
use instro_opcua_test::VariableSpec;
use open62541::ua;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyCFunction;
use pyo3::types::PyDict;

use crate::client::OpcUaClient;
use crate::client::runtime;
use crate::time::Duration;

fn with_server(
    builder: TestServerBuilder,
    body: impl FnOnce(Python<'_>, &Bound<'_, PyDict>, &Arc<TestServer>) -> PyResult<()>,
) -> PyResult<()> {
    let server = Arc::new(
        builder
            .start()
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))?,
    );
    let directory =
        tempfile::tempdir().map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Python::initialize();
    Python::attach(|py| {
        let module = pyo3::wrap_pymodule!(crate::_opcua)(py);
        let locals = PyDict::new(py);
        locals.set_item("opcua", module)?;
        locals.set_item("url", server.endpoint_url())?;
        locals.set_item("namespace", server.namespace_index())?;
        let options = PyDict::new(py);
        options.set_item("application_name", "Instro test")?;
        options.set_item("application_uri", "urn:instro:test")?;
        options.set_item("pki_dir", directory.path())?;
        options.set_item("request_timeout", Duration::from_millis(1_000))?;
        options.set_item("session_retry_limit", 0)?;
        options.set_item("session_retry_initial", Duration::from_millis(10))?;
        options.set_item("session_retry_max", Duration::from_millis(10))?;
        locals.set_item("options", options)?;
        let stop_server = server.clone();
        locals.set_item(
            "stop_server",
            PyCFunction::new_closure(py, None, None, move |_args, _kwargs| {
                stop_server.stop();
                Ok::<_, PyErr>(())
            })?,
        )?;
        locals.set_item(
            "subscription_count",
            PyCFunction::new_closure(py, None, None, |args, _kwargs| {
                let value = args.get_item(0)?;
                let client = value.cast::<OpcUaClient>()?.borrow();
                Ok::<_, PyErr>(client.connection.session.subscription_state().lock().len())
            })?,
        )?;
        let deadline_server = server.clone();
        let deadline = runtime()?.spawn(async move {
            tokio::time::sleep(std::time::Duration::from_secs(10)).await;
            deadline_server.stop();
        });
        let result = body(py, &locals, &server);
        deadline.abort();
        result
    })
}

fn temperature_server() -> TestServerBuilder {
    TestServer::builder().variable(
        1001_u32,
        "Temperature",
        ua::Variant::scalar(ua::Double::new(42.5)),
    )
}

fn run(py: Python<'_>, locals: &Bound<'_, PyDict>, script: &CStr) -> PyResult<()> {
    py.run(script, Some(locals), Some(locals))
}

#[test]
fn node_navigation_enumerates_continuation_pages() -> PyResult<()> {
    let builder = TestServer::builder().add_folder(FolderSpec::new(1000_u32, "Machine"));
    let builder = (1..=3).fold(builder, |builder, index| {
        builder.add_variable(
            VariableSpec::new(1000_u32 + index, format!("Child{index}"))
                .parent(ParentRef::Label("Machine".to_owned()))
                .value(ua::Variant::scalar(ua::Double::new(f64::from(index)))),
        )
    });
    with_server(builder, |py, locals, _| {
        run(
            py,
            locals,
            c"
with opcua.OpcUaClient.connect(url, **options) as client:
    machine = client.objects.get_child(f'/{namespace}:Machine')
    children = machine.browse_children(max_references_per_node=1)
    assert {child.browse_name.value.name for child in children} == {'Child1', 'Child2', 'Child3'}
    assert len(machine.children) == 3
    path = opcua.RelativePath.from_qualified_names([opcua.QualifiedName(namespace, 'Child1')])
    child = machine.get_child(path)
    assert child.node_id == child.get_attribute(opcua.AttributeId.NODE_ID).value
    assert child.browse_path.starting_node == machine.node_id
",
        )
    })
}

#[test]
fn attribute_getters_return_typed_payloads_and_sample_quality() -> PyResult<()> {
    let builder = temperature_server().add_variable(
        VariableSpec::new(1002_u32, "Private")
            .access(Access::WriteOnly)
            .value(ua::Variant::scalar(ua::Double::new(9.0))),
    );
    with_server(builder, |py, locals, _| {
        run(
            py,
            locals,
            c"
with opcua.OpcUaClient.connect(url, **options) as client:
    node = client.objects.get_child(f'/{namespace}:Temperature')
    assert isinstance(node.display_name, opcua.DataValue)
    assert isinstance(node.display_name.value, opcua.LocalizedText)
    assert node.display_name.value.text == 'Temperature'
    assert node.node_class.value == opcua.NodeClass.VARIABLE
    assert node.value.value == 42.5
    unsupported = node.symmetric
    assert isinstance(unsupported, opcua.DataValue)
    assert unsupported.value is None
    assert int(unsupported.status) == 0x80350000
    private = client.objects.get_child(f'/{namespace}:Private').value
    assert isinstance(private, opcua.DataValue)
    assert private.status.is_bad
",
        )
    })
}

#[test]
fn subscription_monitors_the_selected_attribute() -> PyResult<()> {
    with_server(temperature_server(), |py, locals, _| {
        run(
            py,
            locals,
            c"
with opcua.OpcUaClient.connect(url, **options) as client:
    node = client.objects.get_child(f'/{namespace}:Temperature')
    with client.subscribe([node], attribute=opcua.AttributeId.DISPLAY_NAME,
                          publishing_interval=opcua.Duration.from_millis(25)) as subscription:
        source, sample = next(subscription)
        assert source.node_id == node.node_id
        assert sample.value.text == 'Temperature'
    assert list(subscription) == []
",
        )
    })
}

#[test]
fn background_poll_periodically_reads_the_selected_attribute_without_publishing() -> PyResult<()> {
    with_server(temperature_server(), |py, locals, _| {
        run(
            py,
            locals,
            c"
with opcua.OpcUaClient.connect(url, **options) as client:
    node = client.objects.get_child(f'/{namespace}:Temperature')
    with client.subscribe([node], attribute=opcua.AttributeId.DISPLAY_NAME,
                          publishing_enabled=False,
                          poll_interval=opcua.Duration.from_millis(25)) as subscription:
        source, sample = next(subscription)
        assert source.node_id == node.node_id
        assert sample.value.text == 'Temperature'
        source, sample = next(subscription)
        assert source.node_id == node.node_id
        assert sample.value.text == 'Temperature'
",
        )
    })
}

#[test]
fn subscription_delivers_changed_values_with_native_timestamps() -> PyResult<()> {
    with_server(temperature_server(), |py, locals, server| {
        let server = server.clone();
        locals.set_item(
            "set_temperature",
            PyCFunction::new_closure(py, None, None, move |args, _kwargs| {
                let value = args.get_item(0)?.extract::<f64>()?;
                let node_id = server
                    .node_id("Temperature")
                    .ok_or_else(|| PyRuntimeError::new_err("missing fixture Node"))?;
                server
                    .set_value(node_id, ua::Variant::scalar(ua::Double::new(value)))
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))
            })?,
        )?;
        run(
            py,
            locals,
            c"
with opcua.OpcUaClient.connect(url, **options) as client:
    node = client.objects.get_child(f'/{namespace}:Temperature')
    with client.subscribe([node], attribute=opcua.AttributeId.VALUE,
                          publishing_interval=opcua.Duration.from_millis(25)) as subscription:
        assert next(subscription)[1].value == 42.5
        set_temperature(9.5)
        source, sample = next(subscription)
        assert source.node_id == node.node_id
        assert sample.value == 9.5
        assert isinstance(sample.source_timestamp, opcua.DateTime)
        assert isinstance(sample.server_timestamp, opcua.DateTime)
",
        )
    })
}

#[test]
fn rejected_items_are_grouped_and_the_subscription_is_removed() -> PyResult<()> {
    with_server(temperature_server(), |py, locals, _| {
        run(py, locals, c"
with opcua.OpcUaClient.connect(url, **options) as client:
    valid = client.objects.get_child(f'/{namespace}:Temperature')
    missing = [client.get_node(opcua.NodeId(f'ns={namespace};i={i}')) for i in (999991, 999992)]
    try:
        client.subscribe([valid, *missing], attribute=opcua.AttributeId.VALUE)
    except ExceptionGroup as group:
        assert len(group.exceptions) == 2
        assert all(isinstance(error, opcua.OpcUaError) for error in group.exceptions)
        assert [error.node.node_id for error in group.exceptions] == [node.node_id for node in missing]
        assert all(int(error.status_code) == 0x80340000 for error in group.exceptions)
    else:
        raise AssertionError('setup accepted invalid Nodes')
    assert subscription_count(client) == 0
")
    })
}

#[test]
fn invalid_subscription_groups_raise_value_error_without_server_subscriptions() -> PyResult<()> {
    with_server(temperature_server(), |py, locals, _| {
        run(
            py,
            locals,
            c"
with opcua.OpcUaClient.connect(url, **options) as client:
    node = client.objects.get_child(f'/{namespace}:Temperature')
    cases = [
        ([node, node], {}),
        ([node], {'poll_interval': opcua.Duration.from_nanos(0)}),
        ([node], {'poll_interval': opcua.Duration.from_secs(2**64 - 1)}),
    ]
    for nodes, settings in cases:
        try:
            client.subscribe(nodes, attribute=opcua.AttributeId.VALUE, **settings)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid subscription group was accepted')
        assert subscription_count(client) == 0
",
        )
    })
}

#[test]
fn quiet_subscription_waits_until_closed() -> PyResult<()> {
    with_server(temperature_server(), |py, locals, _| {
        run(py, locals, c"
import threading
with opcua.OpcUaClient.connect(url, **options) as client:
    node = client.objects.get_child(f'/{namespace}:Temperature')
    subscription = client.subscribe([node], attribute=opcua.AttributeId.VALUE, publishing_enabled=False)
    entered, finished = threading.Event(), threading.Event()
    output = []
    def consume():
        entered.set()
        output.append(next(subscription, None))
        finished.set()
    consumer = threading.Thread(target=consume)
    consumer.start()
    assert entered.wait(1)
    assert not finished.wait(0.1)
    subscription.close()
    consumer.join(1)
    assert not consumer.is_alive()
    assert output == [None]
")
    })
}
