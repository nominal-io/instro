use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;

use anyhow::Context as _;
use anyhow::Result;
use anyhow::bail;
use opcua::browse::Browse as _;
use opcua::browse::BrowseAll as _;
use opcua::client::OpcUaClient;
use opcua::client::OpcUaClientBuilder;
use opcua::client::OpcUaNodeReadBatch;
use opcua::types::NodeIdInner;
use opcua::types::OpcUaBrowseName;
use opcua::types::OpcUaBrowsePath;
use opcua::types::OpcUaMonitoredItemConfig;
use opcua::types::OpcUaNode;
use opcua::types::OpcUaNodeClass;
use opcua::types::OpcUaNodeId;
use opcua::types::OpcUaNodeReadTarget;
use opcua::types::OpcUaPki;
use opcua::types::OpcUaSample;
use opcua::types::OpcUaSecurityMode;
use opcua::types::OpcUaSecurityPolicy;
use opcua::types::OpcUaSubscriptionConfig;
use opcua::types::OpcUaUserToken;
use opcua::types::OpcUaValue;
use opcua_test::FolderSpec;
use opcua_test::ParentRef;
use opcua_test::TestNodeId;
use opcua_test::TestServer;
use opcua_test::VariableSpec;
use opcua_test::server::LIFETIME_TIMEOUT;
use opcua_test::ua;
use tokio::sync::mpsc;

fn connect_client(server: &TestServer) -> Result<Arc<OpcUaClient>> {
    OpcUaClientBuilder::new()
        .user_identity_token(OpcUaUserToken::anonymous("anonymous".to_owned())?)
        .security_mode(OpcUaSecurityMode::None)
        .security_policy(OpcUaSecurityPolicy::None)
        .timeout(LIFETIME_TIMEOUT)
        .pki(OpcUaPki::None)
        .connect(server.endpoint_url())
}

fn ua_node_id(server: &TestServer, browse_name: &str) -> Result<ua::NodeId> {
    server
        .node_id(browse_name)
        .cloned()
        .with_context(|| format!("test server did not register node `{browse_name}`"))
}

fn opcua_node_id(server: &TestServer, browse_name: &str) -> Result<OpcUaNodeId> {
    let node_id = ua_node_id(server, browse_name)?;
    OpcUaNodeId::try_from(&node_id).with_context(|| format!("converting `{browse_name}` NodeId"))
}

fn opcua_node(
    server: &TestServer,
    browse_name: &str,
    _node_class: OpcUaNodeClass,
) -> Result<OpcUaNodeReadTarget> {
    Ok(OpcUaNodeReadTarget::new(
        opcua_node_id(server, browse_name)?,
        OpcUaBrowsePath::from_segment(OpcUaBrowseName::new(
            server.namespace_index(),
            browse_name.to_owned(),
        )),
    ))
}

fn browse_path(namespace: u16, segments: &[&str]) -> OpcUaBrowsePath {
    segments
        .iter()
        .map(|segment| OpcUaBrowseName::new(namespace, (*segment).to_owned()))
        .collect()
}

async fn root_node(client: &OpcUaClient, node_id: &OpcUaNodeId) -> Result<OpcUaNode> {
    let (browse_name, display_name, node_class) = client.read_node_metadata(node_id).await?;
    Ok(OpcUaNode::new(
        node_id.clone(),
        display_name,
        node_class,
        browse_name,
    ))
}

fn nonzero(value: u32) -> Result<NonZeroU32> {
    NonZeroU32::new(value).with_context(|| format!("expected {value} to be nonzero"))
}

fn subscription_config() -> Result<OpcUaSubscriptionConfig> {
    subscription_config_with_poll(None)
}

fn subscription_config_with_poll(
    background_poll_interval: Option<Duration>,
) -> Result<OpcUaSubscriptionConfig> {
    Ok(OpcUaSubscriptionConfig {
        publishing_interval: Duration::from_millis(25),
        background_poll_interval,
        lifetime_count: 100,
        max_keep_alive_count: nonzero(10)?,
        max_notifications_per_publish: nonzero(16)?,
        priority: 0,
        publishing_enabled: true,
    })
}

fn monitored_item_config() -> OpcUaMonitoredItemConfig {
    OpcUaMonitoredItemConfig {
        sampling_interval: Duration::from_millis(10),
        queue_size: 4,
        discard_oldest: true,
    }
}

fn assert_timestamps_present(samples: &[OpcUaSample]) {
    for sample in samples {
        assert!(
            sample.data.server_timestamp > 0,
            "server timestamp should be populated for {sample:?}",
        );
    }
}

fn sample_value<'a>(samples: &'a [OpcUaSample], node_id: &OpcUaNodeId) -> Option<&'a OpcUaValue> {
    samples
        .iter()
        .rev()
        .find(|sample| &sample.node_id == node_id)
        .map(|sample| &sample.data.value)
}

fn has_value(samples: &[OpcUaSample], node_id: &OpcUaNodeId, expected: &OpcUaValue) -> bool {
    sample_value(samples, node_id).is_some_and(|actual| actual == expected)
}

async fn recv_matching_batch(
    rx: &mut mpsc::UnboundedReceiver<Vec<OpcUaSample>>,
    description: &str,
    mut matches: impl FnMut(&[OpcUaSample]) -> bool,
) -> Result<Vec<OpcUaSample>> {
    let deadline = tokio::time::Instant::now() + LIFETIME_TIMEOUT;

    loop {
        let now = tokio::time::Instant::now();
        if now >= deadline {
            bail!("timed out waiting for {description}");
        }

        let remaining = deadline.saturating_duration_since(now);
        let maybe_samples = tokio::time::timeout(remaining, rx.recv())
            .await
            .with_context(|| format!("timed out waiting for {description}"))?;

        let samples = maybe_samples
            .with_context(|| format!("callback channel closed while waiting for {description}"))?;

        if matches(&samples) {
            return Ok(samples);
        }
    }
}

/// Counts samples received for `node_id` over a fixed `window`, returning when the window
/// elapses or the callback channel closes. Used to observe background-poll cadence for a node
/// that produces no subscription notifications during the window.
async fn count_samples_for(
    rx: &mut mpsc::UnboundedReceiver<Vec<OpcUaSample>>,
    node_id: &OpcUaNodeId,
    window: Duration,
) -> usize {
    let deadline = tokio::time::Instant::now() + window;
    let mut count = 0;

    loop {
        let now = tokio::time::Instant::now();
        if now >= deadline {
            break;
        }

        let remaining = deadline.saturating_duration_since(now);
        match tokio::time::timeout(remaining, rx.recv()).await {
            Ok(Some(samples)) => {
                count += samples
                    .iter()
                    .filter(|sample| &sample.node_id == node_id)
                    .count();
            }

            // Channel closed or the window elapsed: stop counting.
            Ok(None) | Err(_) => break,
        }
    }

    count
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn read_nodes_decodes_samples_in_request_order() -> Result<()> {
    let server = TestServer::builder()
        .variable(
            TestNodeId::Numeric(1000),
            "Temperature",
            ua::Variant::scalar(ua::Double::new(72.5)),
        )
        .variable(
            TestNodeId::String("pressure-node".to_owned()),
            "Pressure",
            ua::Variant::scalar(ua::UInt32::new(101_325)),
        )
        .variable(
            TestNodeId::Guid(ua::Guid::new(
                0x1122_3344,
                0x5566,
                0x7788,
                [0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x00],
            )),
            "Flow",
            ua::Variant::scalar(ua::Float::new(12.5)),
        )
        .variable(
            TestNodeId::ByteString(b"status-node-id".to_vec()),
            "Status",
            ua::Variant::scalar(ua::Int16::new(-7)),
        )
        .variable(
            TestNodeId::Auto,
            "Healthy",
            ua::Variant::scalar(ua::Boolean::new(true)),
        )
        .start()?;

    let pressure = opcua_node(&server, "Pressure", OpcUaNodeClass::Variable)?;
    let flow = opcua_node(&server, "Flow", OpcUaNodeClass::Variable)?;
    let status = opcua_node(&server, "Status", OpcUaNodeClass::Variable)?;
    let healthy = opcua_node(&server, "Healthy", OpcUaNodeClass::Variable)?;
    let temperature = opcua_node(&server, "Temperature", OpcUaNodeClass::Variable)?;

    assert_eq!(flow.node_id.namespace, server.namespace_index());
    assert_eq!(
        flow.node_id.inner,
        NodeIdInner::Guid(uuid::Uuid::from_fields(
            0x1122_3344,
            0x5566,
            0x7788,
            &[0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x00],
        ))
    );
    assert_eq!(status.node_id.namespace, server.namespace_index());
    assert_eq!(
        status.node_id.inner,
        NodeIdInner::ByteString(b"status-node-id".to_vec())
    );

    let nodes = vec![
        pressure.clone(),
        flow.clone(),
        status.clone(),
        healthy.clone(),
        temperature.clone(),
    ];
    let batch = OpcUaNodeReadBatch::new(&nodes, ua::AttributeId::VALUE);
    let client = connect_client(&server)?;

    let samples = client.read_nodes(&batch).await?;
    assert_eq!(samples.len(), 5);
    assert_timestamps_present(&samples);

    let mut samples = samples.iter();
    let pressure_sample = samples.next().context("missing pressure sample")?;
    let flow_sample = samples.next().context("missing flow sample")?;
    let status_sample = samples.next().context("missing status sample")?;
    let healthy_sample = samples.next().context("missing healthy sample")?;
    let temperature_sample = samples.next().context("missing temperature sample")?;
    assert!(samples.next().is_none(), "read returned too many samples");

    assert_eq!(pressure_sample.node_id, pressure.node_id);
    assert_eq!(pressure_sample.data.value, OpcUaValue::UInt32(101_325));
    assert_eq!(flow_sample.node_id, flow.node_id);
    assert_eq!(flow_sample.data.value, OpcUaValue::Float(12.5));
    assert_eq!(status_sample.node_id, status.node_id);
    assert_eq!(status_sample.data.value, OpcUaValue::Int16(-7));
    assert_eq!(healthy_sample.node_id, healthy.node_id);
    assert_eq!(healthy_sample.data.value, OpcUaValue::Boolean(true));
    assert_eq!(temperature_sample.node_id, temperature.node_id);
    assert_eq!(temperature_sample.data.value, OpcUaValue::Double(72.5));

    client.disconnect().await?;
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn browse_node_and_browse_all_return_test_hierarchy() -> Result<()> {
    let server = TestServer::builder()
        .add_folder(FolderSpec::new(TestNodeId::Numeric(2000), "Sensors"))
        .add_folder(
            FolderSpec::new(TestNodeId::Numeric(2001), "Inner")
                .parent(ParentRef::Label("Sensors".to_owned())),
        )
        .add_variable(
            VariableSpec::new(TestNodeId::Numeric(2002), "Temperature")
                .parent(ParentRef::Label("Sensors".to_owned()))
                .value(ua::Variant::scalar(ua::Double::new(72.5))),
        )
        .add_variable(
            VariableSpec::new(TestNodeId::Numeric(2003), "Pressure")
                .parent(ParentRef::Label("Inner".to_owned()))
                .value(ua::Variant::scalar(ua::UInt32::new(101_325))),
        )
        .add_variable(
            VariableSpec::new(
                TestNodeId::Guid(ua::Guid::new(
                    0x2233_4455,
                    0x6677,
                    0x8899,
                    [0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x00, 0x11],
                )),
                "Flow",
            )
            .parent(ParentRef::Label("Sensors".to_owned()))
            .value(ua::Variant::scalar(ua::Float::new(12.5))),
        )
        .add_variable(
            VariableSpec::new(
                TestNodeId::ByteString(b"inner-status-id".to_vec()),
                "Status",
            )
            .parent(ParentRef::Label("Inner".to_owned()))
            .value(ua::Variant::scalar(ua::Int16::new(-7))),
        )
        .start()?;

    let client = connect_client(&server)?;
    let sensors_id = opcua_node_id(&server, "Sensors")?;
    let namespace = server.namespace_index();

    let mut immediate_names = client
        .as_ref()
        .browse_node(sensors_id.clone())
        .await?
        .into_iter()
        .map(|node| (node.browse_name().clone(), node.node_class().clone()))
        .collect::<Vec<_>>();
    immediate_names.sort();

    assert_eq!(
        immediate_names,
        vec![
            (
                OpcUaBrowseName::new(namespace, "Flow".to_owned()),
                OpcUaNodeClass::Variable,
            ),
            (
                OpcUaBrowseName::new(namespace, "Inner".to_owned()),
                OpcUaNodeClass::Object,
            ),
            (
                OpcUaBrowseName::new(namespace, "Temperature".to_owned()),
                OpcUaNodeClass::Variable,
            ),
        ],
    );

    let root = root_node(client.as_ref(), &sensors_id).await?;
    let graph = client.as_ref().browse_all(root, None).await?;
    let temperature_path = browse_path(namespace, &["Sensors", "Temperature"]);
    let temperature = graph
        .resolve_path(&temperature_path)
        .next()
        .context("browse_all omitted Temperature")?;
    assert_eq!(temperature.node().node_class(), &OpcUaNodeClass::Variable);
    assert!(
        graph.children(temperature.id()).next().is_none(),
        "Temperature has no children in this test server",
    );
    assert_eq!(temperature.browse_path(), &temperature_path);

    let flow_path = browse_path(namespace, &["Sensors", "Flow"]);
    let flow = graph
        .resolve_path(&flow_path)
        .next()
        .context("browse_all omitted Flow")?;
    assert_eq!(flow.node().node_class(), &OpcUaNodeClass::Variable);
    assert!(
        graph.children(flow.id()).next().is_none(),
        "Flow has no children in this test server"
    );
    assert_eq!(flow.browse_path(), &flow_path);
    assert_eq!(flow.node().node_id().namespace, namespace);
    assert_eq!(
        &flow.node().node_id().inner,
        &NodeIdInner::Guid(uuid::Uuid::from_fields(
            0x2233_4455,
            0x6677,
            0x8899,
            &[0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x00, 0x11],
        ))
    );

    let inner_path = browse_path(namespace, &["Sensors", "Inner"]);
    let inner = graph
        .resolve_path(&inner_path)
        .next()
        .context("browse_all omitted Inner folder")?;
    assert_eq!(inner.node().node_class(), &OpcUaNodeClass::Object);
    assert_eq!(inner.browse_path(), &inner_path);

    let pressure_path = browse_path(namespace, &["Sensors", "Inner", "Pressure"]);
    let pressure = graph
        .resolve_path(&pressure_path)
        .next()
        .context("browse_all omitted nested Pressure node")?;
    assert_eq!(pressure.node().node_class(), &OpcUaNodeClass::Variable);
    assert_eq!(pressure.browse_path(), &pressure_path);

    let status_path = browse_path(namespace, &["Sensors", "Inner", "Status"]);
    let status = graph
        .resolve_path(&status_path)
        .next()
        .context("browse_all omitted nested Status node")?;
    assert_eq!(status.node().node_class(), &OpcUaNodeClass::Variable);
    assert_eq!(status.browse_path(), &status_path);
    assert_eq!(status.node().node_id().namespace, namespace);
    assert_eq!(
        &status.node().node_id().inner,
        &NodeIdInner::ByteString(b"inner-status-id".to_vec())
    );

    let (metadata_name, display_name, node_class) = client.read_node_metadata(&sensors_id).await?;
    assert_eq!(metadata_name.name, "Sensors");
    assert_eq!(display_name, "Sensors");
    assert_eq!(node_class, OpcUaNodeClass::Object);

    client.disconnect().await?;
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn start_polling_emits_batches_and_stops_cleanly() -> Result<()> {
    let server = TestServer::builder()
        .variable(
            TestNodeId::Numeric(3000),
            "Temperature",
            ua::Variant::scalar(ua::Double::new(1.0)),
        )
        .start()?;

    let temperature_node = opcua_node(&server, "Temperature", OpcUaNodeClass::Variable)?;
    let temperature_ua_id = ua_node_id(&server, "Temperature")?;
    let (tx, mut rx) = mpsc::unbounded_channel();
    let client = connect_client(&server)?;

    let session = client.start_polling(
        vec![temperature_node.clone()],
        Duration::from_millis(25),
        move |samples| {
            let _ = tx.send(samples.collect::<Vec<_>>());
        },
    )?;

    let initial = recv_matching_batch(&mut rx, "initial polling batch", |samples| {
        has_value(samples, &temperature_node.node_id, &OpcUaValue::Double(1.0))
    })
    .await?;
    assert_eq!(initial.len(), 1);
    assert_timestamps_present(&initial);

    server.set_value(
        &temperature_ua_id,
        ua::Variant::scalar(ua::Double::new(2.5)),
    )?;

    let changed = recv_matching_batch(&mut rx, "updated polling batch", |samples| {
        has_value(samples, &temperature_node.node_id, &OpcUaValue::Double(2.5))
    })
    .await?;
    assert_eq!(changed.len(), 1);
    assert_timestamps_present(&changed);

    session.stop_timeout(LIFETIME_TIMEOUT)?;
    client.disconnect().await?;
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn start_subscription_emits_changes_and_stops_cleanly() -> Result<()> {
    let server = TestServer::builder()
        .variable(
            TestNodeId::Numeric(4000),
            "Temperature",
            ua::Variant::scalar(ua::Double::new(1.0)),
        )
        .start()?;

    let temperature_node = opcua_node(&server, "Temperature", OpcUaNodeClass::Variable)?;
    let temperature_ua_id = ua_node_id(&server, "Temperature")?;
    let (tx, mut rx) = mpsc::unbounded_channel();
    let client = connect_client(&server)?;

    let session = client
        .start_subscription(
            vec![temperature_node.clone()],
            subscription_config()?,
            monitored_item_config(),
            move |samples| {
                let _ = tx.send(samples.collect::<Vec<_>>());
            },
        )
        .await?;

    server.set_value(
        &temperature_ua_id,
        ua::Variant::scalar(ua::Double::new(9.5)),
    )?;

    let changed = recv_matching_batch(&mut rx, "subscription change", |samples| {
        has_value(samples, &temperature_node.node_id, &OpcUaValue::Double(9.5))
    })
    .await?;
    assert_eq!(
        sample_value(&changed, &temperature_node.node_id),
        Some(&OpcUaValue::Double(9.5)),
    );
    assert_timestamps_present(&changed);

    session.stop_timeout(LIFETIME_TIMEOUT)?;
    client.disconnect().await?;
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn background_polling_emits_periodic_samples_for_static_node() -> Result<()> {
    let server = TestServer::builder()
        .variable(
            TestNodeId::Numeric(5000),
            "Static",
            ua::Variant::scalar(ua::Double::new(42.0)),
        )
        .start()?;

    let static_node = opcua_node(&server, "Static", OpcUaNodeClass::Variable)?;
    let (tx, mut rx) = mpsc::unbounded_channel();
    let client = connect_client(&server)?;

    let session = client
        .start_subscription(
            vec![static_node.clone()],
            subscription_config_with_poll(Some(Duration::from_millis(50)))?,
            monitored_item_config(),
            move |samples| {
                let _ = tx.send(samples.collect::<Vec<_>>());
            },
        )
        .await?;

    // A monitored item reports its node's value once on creation. Consume that initial
    // subscription notification so that subsequent samples are unambiguously attributable to
    // background polling: the node's value is never changed, so the subscription stays silent.
    recv_matching_batch(&mut rx, "initial subscription value", |samples| {
        has_value(samples, &static_node.node_id, &OpcUaValue::Double(42.0))
    })
    .await?;

    // Over the window, the never-changing node yields no further notifications, so any samples
    // collected here must be background polls. The window spans many poll intervals; require at
    // least two to confirm polling is periodic rather than a one-off.
    let polled = count_samples_for(&mut rx, &static_node.node_id, Duration::from_millis(750)).await;
    assert!(
        polled >= 2,
        "expected at least two background-polled samples for the static node, got {polled}",
    );

    session.stop_timeout(LIFETIME_TIMEOUT)?;
    client.disconnect().await?;
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn disconnect_reports_outstanding_references_before_graceful_disconnect() -> Result<()> {
    let server = TestServer::builder().start()?;
    let client = connect_client(&server)?;
    let extra_ref = Arc::clone(&client);

    match client.disconnect().await {
        Ok(()) => bail!("disconnect should reject outstanding client references"),
        Err(e) => {
            let message = e.to_string();
            assert!(
                message.contains("outstanding references"),
                "unexpected disconnect error: {message}",
            );
        }
    }

    extra_ref.disconnect().await?;
    Ok(())
}
