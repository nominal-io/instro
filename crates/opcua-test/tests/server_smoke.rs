//! End-to-end tests against the in-process [`TestServer`].
//!
//! These tests connect a real [`open62541::AsyncClient`] to a real test server
//! over TCP and exercise stored variables, access levels, folder hierarchies,
//! and lifecycle.

use opcua_test::Access;
use opcua_test::DataSource;
use opcua_test::DataSourceReadContext;
use opcua_test::DataSourceResult;
use opcua_test::MethodSpec;
use opcua_test::ParentRef;
use opcua_test::TestNodeId;
use opcua_test::TestServer;
use opcua_test::ValueSource;
use opcua_test::VariableSpec;
use opcua_test::server::LIFETIME_TIMEOUT;
use opcua_test::ua;
use open62541::AsyncClient;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn rw_stored_variable_round_trip() {
    let server = TestServer::builder()
        .variable(
            TestNodeId::Numeric(1000),
            "Temperature",
            ua::Variant::scalar(ua::Double::new(72.5)),
        )
        .start()
        .expect("test server starts");

    let temperature_id = server
        .node_id("Temperature")
        .cloned()
        .expect("Temperature node id is registered");

    let client = AsyncClient::new(server.endpoint_url()).expect("client connects");

    // Initial read returns the seeded value.
    let initial = tokio::time::timeout(LIFETIME_TIMEOUT, client.read_value(&temperature_id))
        .await
        .expect("read_value did not time out")
        .expect("read_value succeeds");

    let initial_scalar = initial
        .value()
        .and_then(ua::Variant::as_scalar::<ua::Double>)
        .map(ua::Double::value)
        .expect("initial value is a Double");

    assert!(
        (initial_scalar - 72.5).abs() < f64::EPSILON,
        "initial read returned {initial_scalar:?}",
    );

    // Client write reflects on subsequent read.
    client
        .write_value(
            &temperature_id,
            &ua::DataValue::new(ua::Variant::scalar(ua::Double::new(99.25))),
        )
        .await
        .expect("client write_value succeeds on RW variable");

    let after_client_write = client
        .read_value(&temperature_id)
        .await
        .expect("read after client write")
        .value()
        .and_then(ua::Variant::as_scalar::<ua::Double>)
        .map(ua::Double::value)
        .expect("post-write value is a Double");

    assert!(
        (after_client_write - 99.25).abs() < f64::EPSILON,
        "post-write read returned {after_client_write:?}",
    );

    // Server-side `set_value` is also reflected.
    server
        .set_value(&temperature_id, ua::Variant::scalar(ua::Double::new(0.0)))
        .expect("server-side set_value");

    let after_server_set = client
        .read_value(&temperature_id)
        .await
        .expect("read after server set")
        .value()
        .and_then(ua::Variant::as_scalar::<ua::Double>)
        .map(ua::Double::value)
        .expect("post-server-set value is a Double");

    assert!(
        after_server_set.abs() < f64::EPSILON,
        "post-server-set read returned {after_server_set:?}",
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn read_only_variable_rejects_writes() {
    let server = TestServer::builder()
        .add_variable(
            VariableSpec::new(TestNodeId::Numeric(2000), "Pressure")
                .access(Access::ReadOnly)
                .value(ua::Variant::scalar(ua::UInt32::new(101_325))),
        )
        .start()
        .expect("test server starts");

    let pressure_id = server
        .node_id("Pressure")
        .cloned()
        .expect("Pressure node id is registered");

    let client = AsyncClient::new(server.endpoint_url()).expect("client connects");

    // Read works.
    let value = client
        .read_value(&pressure_id)
        .await
        .expect("read on RO succeeds")
        .value()
        .and_then(ua::Variant::as_scalar::<ua::UInt32>)
        .map(ua::UInt32::value)
        .expect("RO value is a UInt32");

    assert_eq!(value, 101_325);

    // Write must be rejected by the server.
    let write_result = client
        .write_value(
            &pressure_id,
            &ua::DataValue::new(ua::Variant::scalar(ua::UInt32::new(0))),
        )
        .await;

    assert!(
        write_result.is_err(),
        "RO variable should reject client writes; got {write_result:?}",
    );

    drop(server);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn folder_hierarchy_resolves_via_label() {
    use opcua_test::FolderSpec;

    let server = TestServer::builder()
        .add_folder(FolderSpec::new(TestNodeId::Auto, "Sensors"))
        .add_variable(
            VariableSpec::new(TestNodeId::Auto, "InnerTemp")
                .parent(ParentRef::Label("Sensors".into()))
                .value(ua::Variant::scalar(ua::Int16::new(42))),
        )
        .start()
        .expect("server with folder hierarchy starts");

    let inner_id = server
        .node_id("InnerTemp")
        .cloned()
        .expect("InnerTemp registered");

    let client = AsyncClient::new(server.endpoint_url()).expect("client connects");

    let value = client
        .read_value(&inner_id)
        .await
        .expect("read on nested var")
        .value()
        .and_then(ua::Variant::as_scalar::<ua::Int16>)
        .map(ua::Int16::value)
        .expect("inner temp is Int16");

    assert_eq!(value, 42);
}

#[test]
#[should_panic(expected = "method nodes are not yet wired through")]
fn method_spec_panics_until_implemented() {
    struct DummyMethod;
    impl open62541::MethodCallback for DummyMethod {
        fn call(
            &mut self,
            _ctx: &mut open62541::MethodCallbackContext,
        ) -> open62541::MethodCallbackResult {
            Ok(())
        }
    }

    let _ = TestServer::builder()
        .add_method(MethodSpec::new(
            TestNodeId::Numeric(9000),
            "DoStuff",
            DummyMethod,
        ))
        .start();
}

#[test]
#[should_panic(expected = "data-source variables are not yet wired through")]
fn data_source_variable_panics_until_implemented() {
    struct DummySource;
    impl DataSource for DummySource {
        fn read(&mut self, _ctx: &mut DataSourceReadContext) -> DataSourceResult {
            Ok(())
        }
    }

    let _ = TestServer::builder()
        .add_variable(
            VariableSpec::new(TestNodeId::Auto, "Dynamic")
                .source(ValueSource::DataSource(Box::new(DummySource)))
                .data_type(ua::NodeId::ns0(open62541_sys::UA_NS0ID_INT32)),
        )
        .start();
}
