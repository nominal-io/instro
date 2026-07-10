//! Stream-plane integration tests: reconcile a rack, then stream from the sim
//! and verify batches, rates, epoch restarts, gaps, and disconnects.

use instro_quantus_rs::blocking::QuantusClient;
use instro_quantus_rs::config::RackConfig;
use instro_quantus_rs::stream::{StreamEngine, StreamEvent};
use quantus_sim::rest::SimServer;
use std::collections::HashMap;
use std::time::{Duration, Instant};

fn start_sim(faults: &str) -> SimServer {
    let sim_config: quantus_sim::config::SimConfig = toml::from_str(&format!(
        r#"
        [system]
        chassis = "MicroQ"
        serial = "SIM0001"
        master_sampling_rate = 131072

        [server]
        rest_port = 0
        stream_port = 0

        [[slots]]
        slot = 1
        module = "MIC42X7"

        [[slots.channels]]
        index = 1
        signal = {{ kind = "sine", frequency_hz = 1000.0, amplitude = 0.5 }}

        [[slots]]
        slot = 2
        module = "THM427"

        [[slots.channels]]
        index = 1
        signal = {{ kind = "constant", value = 21.5 }}

        {faults}
        "#
    ))
    .unwrap();
    SimServer::start(sim_config).unwrap()
}

fn reconcile(sim: &SimServer) -> QuantusClient {
    let rack: RackConfig = toml::from_str(&format!(
        r#"
        [device]
        name = "test_rig"

        [connection]
        host = "127.0.0.1"
        port = {}

        [system]
        master_sampling_rate = 131072

        [[modules]]
        name = "MIC42X7"
        sample_rate_hz = 65536.0

        [[modules.channels]]
        index = 1
        alias = "mic"
        mode = "Microphone Input"
        streaming = true

        [[modules]]
        name = "THM427"
        sample_rate_hz = 512.0

        [[modules.channels]]
        index = 1
        alias = "tc"
        mode = "Thermocouple Type K Input"
        streaming = true
        "#,
        sim.rest_port()
    ))
    .unwrap();
    let client = QuantusClient::connect(rack).unwrap();
    client.reconcile().unwrap();
    client
}

/// Collect events until `deadline`, returning analog sample counts per channel.
fn collect_samples(
    engine: &StreamEngine,
    duration: Duration,
) -> (HashMap<i32, usize>, Vec<StreamEvent>) {
    let deadline = Instant::now() + duration;
    let mut counts: HashMap<i32, usize> = HashMap::new();
    let mut other = Vec::new();
    while Instant::now() < deadline {
        match engine.events().recv_timeout(Duration::from_millis(200)) {
            Ok(StreamEvent::Analog(batch)) => {
                *counts.entry(batch.channel_id).or_default() += batch.samples.len();
            }
            Ok(event) => other.push(event),
            Err(_) => {}
        }
    }
    (counts, other)
}

#[test]
fn streams_analog_batches_at_configured_rates() {
    let sim = start_sim("");
    let client = reconcile(&sim);
    let setup = client.data_stream_setup().unwrap();
    assert_eq!(setup["TCPPort"], sim.stream_port());

    let engine = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();
    let (counts, _) = collect_samples(&engine, Duration::from_millis(600));

    assert_eq!(
        counts.len(),
        2,
        "expected two streaming channels: {counts:?}"
    );
    let (&fast_id, &fast) = counts.iter().max_by_key(|&(_, &v)| v).unwrap();
    let (&slow_id, &slow) = counts.iter().min_by_key(|&(_, &v)| v).unwrap();
    assert_ne!(fast_id, slow_id);
    // 65536 Hz vs 512 Hz = 128x; allow generous slack for timing jitter.
    assert!(fast > 60 * slow, "rate ratio off: fast={fast} slow={slow}");
    assert!(slow > 0);

    let health = engine.health();
    assert!(health.packets > 0);
    assert_eq!(health.epoch_restarts, 0);
    engine.stop();
}

#[test]
fn signal_values_match_definitions() {
    let sim = start_sim("");
    let _client = reconcile(&sim);
    let engine = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();

    let deadline = Instant::now() + Duration::from_millis(600);
    let mut sine_ok = false;
    let mut constant_ok = false;
    while Instant::now() < deadline && !(sine_ok && constant_ok) {
        if let Ok(StreamEvent::Analog(batch)) =
            engine.events().recv_timeout(Duration::from_millis(200))
        {
            if batch.samples.iter().all(|s| (*s - 21.5).abs() < 1e-6) {
                constant_ok = true;
            } else if batch.samples.iter().all(|s| s.abs() <= 0.5 + 1e-4) {
                sine_ok = true;
            }
        }
    }
    assert!(sine_ok, "no sine batches within amplitude bounds");
    assert!(constant_ok, "no constant 21.5 batches");
    engine.stop();
}

#[test]
fn apply_mid_stream_restarts_the_epoch() {
    let sim = start_sim("");
    let client = reconcile(&sim);
    let engine = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();
    // Let some packets flow, then reconcile again (pending -> apply -> restart).
    let (_, _) = collect_samples(&engine, Duration::from_millis(300));
    client.reconcile().unwrap();

    let deadline = Instant::now() + Duration::from_secs(3);
    let mut restarted = false;
    while Instant::now() < deadline {
        if let Ok(StreamEvent::EpochRestart { .. }) =
            engine.events().recv_timeout(Duration::from_millis(200))
        {
            restarted = true;
            break;
        }
    }
    assert!(restarted, "no EpochRestart after mid-stream apply");
    assert!(engine.health().epoch_restarts >= 1);
    engine.stop();
}

#[test]
fn dropped_packets_surface_as_gaps() {
    let sim = start_sim("[faults]\ndrop_every_nth_packet = 3");
    let _client = reconcile(&sim);
    let engine = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();

    let (_, events) = collect_samples(&engine, Duration::from_millis(600));
    let gaps = events
        .iter()
        .filter(|e| matches!(e, StreamEvent::Gap { .. }))
        .count();
    assert!(gaps >= 2, "expected gaps from dropped packets, got {gaps}");
    assert!(engine.health().missing_packets >= 2);
    engine.stop();
}

#[test]
fn disconnect_fault_surfaces_as_event() {
    let sim = start_sim("[faults]\ndisconnect_after_packets = 3");
    let _client = reconcile(&sim);
    let engine = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();

    let deadline = Instant::now() + Duration::from_secs(5);
    let mut disconnected = false;
    while Instant::now() < deadline {
        match engine.events().recv_timeout(Duration::from_millis(200)) {
            Ok(StreamEvent::Disconnected { .. }) => {
                disconnected = true;
                break;
            }
            Ok(_) => {}
            Err(mpsc_err) => {
                if matches!(mpsc_err, std::sync::mpsc::RecvTimeoutError::Disconnected) {
                    disconnected = true;
                    break;
                }
            }
        }
    }
    assert!(disconnected, "no disconnect after fault");
}

#[test]
fn second_streaming_client_is_rejected() {
    let sim = start_sim("");
    let _client = reconcile(&sim);
    let first = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();
    // Ensure the first connection is being served.
    let (counts, _) = collect_samples(&first, Duration::from_millis(200));
    assert!(!counts.is_empty());

    let second = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut rejected = false;
    while Instant::now() < deadline {
        match second.events().recv_timeout(Duration::from_millis(200)) {
            Ok(StreamEvent::Disconnected { .. }) => {
                rejected = true;
                break;
            }
            Ok(StreamEvent::Analog(_)) => panic!("second client received data"),
            Ok(_) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                rejected = true;
                break;
            }
            Err(_) => {}
        }
    }
    assert!(rejected, "second client was not rejected");
    first.stop();
}

#[test]
fn suspend_and_resume_gap_the_stream() {
    let sim = start_sim("");
    let _client = reconcile(&sim);
    let engine = StreamEngine::connect("127.0.0.1", sim.stream_port()).unwrap();
    let (before, _) = collect_samples(&engine, Duration::from_millis(300));
    assert!(!before.is_empty());

    // Suspend via REST; data is discarded (sequence gaps), then resumes.
    ureq_put(&format!(
        "http://127.0.0.1:{}/dataStream/suspend/",
        client_rest_port(&sim)
    ));
    std::thread::sleep(Duration::from_millis(300));
    ureq_put(&format!(
        "http://127.0.0.1:{}/dataStream/resume/",
        client_rest_port(&sim)
    ));

    let deadline = Instant::now() + Duration::from_secs(3);
    let mut saw_gap = false;
    let mut saw_data_after = false;
    while Instant::now() < deadline && !(saw_gap && saw_data_after) {
        match engine.events().recv_timeout(Duration::from_millis(200)) {
            Ok(StreamEvent::Gap { .. }) => saw_gap = true,
            Ok(StreamEvent::Analog(_)) if saw_gap => saw_data_after = true,
            _ => {}
        }
    }
    assert!(saw_gap, "suspend produced no sequence gap");
    assert!(saw_data_after, "no data after resume");
    engine.stop();
}

fn client_rest_port(sim: &SimServer) -> u16 {
    sim.rest_port()
}

fn ureq_put(url: &str) {
    ureq::put(url).call().unwrap();
}
