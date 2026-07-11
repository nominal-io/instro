//! Phase 4 integration tests: tacho events, CAN frames + transmit, Raw-mode
//! fixed-point streaming, and the sustained-rate benchmark (ignored by
//! default; run with --ignored --release).

use instro_quantus_rs::blocking::QuantusClient;
use instro_quantus_rs::config::RackConfig;
use instro_quantus_rs::error::Error;
use instro_quantus_rs::stream::{StreamEngine, StreamEvent};
use quantus_sim::rest::SimServer;
use serde_json::json;
use std::time::{Duration, Instant};

fn start_sim(extra: &str) -> SimServer {
    let sim_config: quantus_sim::config::SimConfig = toml::from_str(&format!(
        r#"
        [system]
        chassis = "MicroQ"
        serial = "SIM0001"
        master_sampling_rate = 131072

        [server]
        rest_port = 0
        stream_port = 0

        {extra}
        "#
    ))
    .unwrap();
    SimServer::start(sim_config).unwrap()
}

fn connect_and_reconcile(sim: &SimServer, modules_toml: &str) -> QuantusClient {
    let rack: RackConfig = toml::from_str(&format!(
        r#"
        [device]
        name = "test_rig"

        [connection]
        host = "127.0.0.1"
        port = {}

        [system]
        master_sampling_rate = 131072

        {modules_toml}
        "#,
        sim.rest_port()
    ))
    .unwrap();
    let client = QuantusClient::connect(rack).unwrap();
    client.reconcile().unwrap();
    client
}

#[test]
fn tacho_events_and_can_frames_stream() {
    let sim = start_sim(
        r#"
        [[slots]]
        slot = 1
        module = "ICT42S6"

        [[slots.channels]]
        index = 1
        signal = { kind = "rpm", rpm = 3000.0 }

        [[slots]]
        slot = 2
        module = "CAN42S2"

        [[slots.channels]]
        index = 1
        signal = { kind = "constant", value = 0.0 }
        playback = [{ id = 0x18FF50E5, period_ms = 10, dlc = 8 }]
        "#,
    );
    let _client = connect_and_reconcile(
        &sim,
        r#"
        [[modules]]
        name = "ICT42S6"

        [[modules.channels]]
        index = 1
        alias = "shaft"
        mode = "Enabled"
        streaming = true

        [[modules]]
        name = "CAN42S2"

        [[modules.channels]]
        index = 1
        alias = "vehicle_bus"
        mode = "Listen Only"
        streaming = true
        "#,
    );

    let engine = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();
    let deadline = Instant::now() + Duration::from_millis(800);
    let mut edges: Vec<f64> = Vec::new();
    let mut frames = Vec::new();
    while Instant::now() < deadline {
        match engine.events().recv_timeout(Duration::from_millis(200)) {
            Ok(StreamEvent::Tacho { events_ms, .. }) => edges.extend(events_ms),
            Ok(StreamEvent::Can {
                frames: mut received,
                ..
            }) => frames.append(&mut received),
            _ => {}
        }
    }

    // 3000 rpm = 50 pulses/s -> ~35+ edges in 0.8 s, 20 ms apart.
    assert!(edges.len() >= 20, "too few tacho edges: {}", edges.len());
    let deltas: Vec<f64> = edges.windows(2).map(|w| w[1] - w[0]).collect();
    assert!(
        deltas.iter().all(|d| (*d - 20.0).abs() < 0.5),
        "tacho intervals not ~20 ms: {deltas:?}"
    );

    // 10 ms period -> ~60+ frames in 0.8 s, all with the playback id and a
    // counter payload.
    assert!(frames.len() >= 40, "too few CAN frames: {}", frames.len());
    assert!(frames.iter().all(|f| f.id == 0x18FF50E5));
    assert!(frames.iter().all(|f| f.data.len() == 8));
    assert_eq!(frames[0].data[0], 0);
    assert_eq!(frames[1].data[0], 1);
    let timestamps: Vec<f64> = frames.iter().map(|f| f.timestamp_s).collect();
    assert!(
        timestamps.windows(2).all(|w| w[1] > w[0]),
        "frame timestamps not monotonic"
    );
    engine.stop();
}

#[test]
fn raw_mode_streams_fixed_point_that_decodes_to_volts() {
    let sim = start_sim(
        r#"
        [[slots]]
        slot = 1
        module = "ICS425"

        [[slots.channels]]
        index = 1
        signal = { kind = "constant", value = 1.25 }
        "#,
    );
    let rack_extra = r#"
        [system]
        streaming_format = "Raw"

        [[modules]]
        name = "ICS425"

        [[modules.channels]]
        index = 1
        alias = "accel"
        mode = "Voltage Input"
        streaming = true
        "#;
    // streaming_format lives under [system]; splice it in via the shared helper.
    let rack: RackConfig = toml::from_str(&format!(
        r#"
        [device]
        name = "test_rig"

        [connection]
        host = "127.0.0.1"
        port = {}

        [system]
        master_sampling_rate = 131072
        {rest}
        "#,
        sim.rest_port(),
        rest = rack_extra.trim_start().strip_prefix("[system]").unwrap()
    ))
    .unwrap();
    let client = QuantusClient::connect(rack).unwrap();
    client.reconcile().unwrap();

    let engine = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();
    let deadline = Instant::now() + Duration::from_millis(800);
    let mut checked = false;
    while Instant::now() < deadline && !checked {
        if let Ok(StreamEvent::Analog(batch)) =
            engine.events().recv_timeout(Duration::from_millis(200))
        {
            assert!(!batch.samples.is_empty());
            for sample in &batch.samples {
                assert!(
                    (sample - 1.25).abs() < 1e-4,
                    "raw-mode decode off: {sample}"
                );
            }
            checked = true;
        }
    }
    assert!(checked, "no raw-mode analog batches received");
    engine.stop();
}

#[test]
fn can_transmit_requires_participate_mode() {
    let sim = start_sim(
        r#"
        [[slots]]
        slot = 1
        module = "CAN42S2"
        "#,
    );
    let client = connect_and_reconcile(
        &sim,
        r#"
        [[modules]]
        name = "CAN42S2"

        [[modules.channels]]
        index = 1
        alias = "bus_listen"
        mode = "Listen Only"

        [[modules.channels]]
        index = 2
        alias = "bus_tx"
        mode = "Participate"
        "#,
    );

    let tree = client.discover().unwrap();
    let can_module = tree.modules.iter().find(|m| m.name == "CAN42S2").unwrap();
    let listen_id = can_module.channel_ids[0];
    let participate_id = can_module.channel_ids[1];

    let message_list = json!({ "MessageList": [
        { "Id": 0x123, "Data": [1, 2, 3, 4] }
    ]});
    client
        .put_can_message_list(participate_id, &message_list)
        .unwrap();
    client.can_transmit(participate_id).unwrap();
    assert_eq!(
        sim.state.lock().unwrap().can_transmits.get(&participate_id),
        Some(&1)
    );

    // Listen-only channel refuses transmit (StatusCode 20).
    match client.can_transmit(listen_id) {
        Err(Error::Api { status, .. }) => assert_eq!(status.status_code, 20),
        other => panic!("expected StatusCode 20, got {other:?}"),
    }

    // Non-CAN item refuses the whole endpoint family (StatusCode 19).
    match client.can_transmit(1) {
        Err(Error::Api { status, .. }) => assert_eq!(status.status_code, 19),
        other => panic!("expected StatusCode 19, got {other:?}"),
    }
}

#[test]
fn write_paths_action_and_settings_plane() {
    let sim = start_sim(
        r#"
        [[slots]]
        slot = 1
        module = "WSB42X2"
        "#,
    );
    let client = connect_and_reconcile(
        &sim,
        r#"
        [[modules]]
        name = "WSB42X2"

        [[modules.channels]]
        index = 1
        alias = "strain_1"
        mode = "WSB Input: Voltage Excitation"
        streaming = true
        "#,
    );

    // Action-plane: no apply, no epoch impact.
    let epoch_before = sim.state.lock().unwrap().epoch;
    client.auto_zero(None).unwrap();
    let tree = client.discover().unwrap();
    let wsb_channel = tree.modules[0].channel_ids[0];
    client.bridge_balance(Some(wsb_channel)).unwrap();
    assert_eq!(sim.state.lock().unwrap().epoch, epoch_before);

    // Settings-plane: write + apply, reporting the epoch restart.
    let mut values = std::collections::BTreeMap::new();
    values.insert(
        "Excitation Amplitude".to_string(),
        instro_quantus_rs::config::SettingValue::Number(2.5),
    );
    let restarted = client.write_settings(wsb_channel, &values).unwrap();
    assert!(
        restarted,
        "settings-plane write should report epoch restart"
    );
    {
        let state = sim.state.lock().unwrap();
        assert_eq!(state.epoch, epoch_before + 1);
        let channel = state
            .items
            .iter()
            .find(|i| i.item_id == wsb_channel)
            .unwrap();
        let amplitude = channel
            .settings
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["Name"] == "Excitation Amplitude")
            .unwrap()["Value"]
            .clone();
        assert_eq!(amplitude, 2.5);
    }

    // ValidationLimits enforced client-side: 0..10 V.
    let mut bad = std::collections::BTreeMap::new();
    bad.insert(
        "Excitation Amplitude".to_string(),
        instro_quantus_rs::config::SettingValue::Number(42.0),
    );
    match client.write_settings(wsb_channel, &bad) {
        Err(Error::Config(message)) => assert!(message.contains("limits"), "{message}"),
        other => panic!("expected limits error, got {other:?}"),
    }
}

#[test]
fn alo_output_configure_and_write() {
    let sim = start_sim(
        r#"
        [[slots]]
        slot = 1
        module = "ALO42S4"
        "#,
    );
    let client = connect_and_reconcile(
        &sim,
        r#"
        [[modules]]
        name = "ALO42S4"

        [[modules.channels]]
        index = 1
        alias = "shaker_drive"
        mode = "Sine Wave Generator"
        settings = { "Signal Amplitude" = 2.0, "Signal Frequency" = 100.0, "Signal Connection" = "Connected" }
        "#,
    );

    let tree = client.discover().unwrap();
    let alo_channel = tree.modules[0].channel_ids[0];
    {
        let state = sim.state.lock().unwrap();
        let channel = state
            .items
            .iter()
            .find(|i| i.item_id == alo_channel)
            .unwrap();
        assert_eq!(channel.current_mode, 2); // Sine
        let amplitude = channel
            .settings
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["Name"] == "Signal Amplitude")
            .unwrap()["Value"]
            .clone();
        assert_eq!(amplitude, 2.0);
    }

    // Settings-plane output write (D12): retarget amplitude, epoch restarts.
    let mut values = std::collections::BTreeMap::new();
    values.insert(
        "Signal Amplitude".to_string(),
        instro_quantus_rs::config::SettingValue::Number(5.0),
    );
    let restarted = client.write_settings(alo_channel, &values).unwrap();
    assert!(restarted);

    // Amplitude beyond the ±10 V hardware range is rejected client-side.
    let mut bad = std::collections::BTreeMap::new();
    bad.insert(
        "Signal Amplitude".to_string(),
        instro_quantus_rs::config::SettingValue::Number(11.0),
    );
    assert!(matches!(
        client.write_settings(alo_channel, &bad),
        Err(Error::Config(_))
    ));
}

/// Sustained-rate benchmark: 4 MIC42X7 modules at MSR/1 (24 channels x
/// 131072 Sa/s ~ 12.6 MB/s) for 3 seconds with zero sequence gaps.
/// Run with: cargo test --release -p quantus-client --test phase4_sim -- --ignored
#[test]
#[ignore]
fn benchmark_sustained_rate_no_gaps() {
    let slots: String = (1..=4)
        .map(|i| {
            format!(
                r#"
        [[slots]]
        slot = {i}
        module = "MIC42X7"
        "#
            )
        })
        .collect();
    let sim = start_sim(&slots);

    let modules: String = (0..4)
        .map(|occ| {
            let channels: String = (1..=6)
                .map(|idx| {
                    format!(
                        r#"
        [[modules.channels]]
        index = {idx}
        mode = "Voltage Input"
        streaming = true
        "#
                    )
                })
                .collect();
            format!(
                r#"
        [[modules]]
        name = "MIC42X7"
        occurrence = {occ}
        sample_rate_hz = 131072.0
        {channels}
        "#
            )
        })
        .collect();
    let _client = connect_and_reconcile(&sim, &modules);

    let engine = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut total_samples: u64 = 0;
    while Instant::now() < deadline {
        if let Ok(StreamEvent::Analog(batch)) =
            engine.events().recv_timeout(Duration::from_millis(200))
        {
            total_samples += batch.samples.len() as u64;
        }
    }
    let health = engine.health();
    let expected = 24u64 * 131072 * 3;
    println!(
        "benchmark: {total_samples} samples ({:.1}% of nominal), {} packets, {} gaps ({} missing)",
        100.0 * total_samples as f64 / expected as f64,
        health.packets,
        health.gaps,
        health.missing_packets,
    );
    assert_eq!(health.gaps, 0, "sequence gaps under sustained load");
    assert!(
        total_samples as f64 > expected as f64 * 0.8,
        "throughput too low: {total_samples} of {expected}"
    );
    engine.stop();
}
