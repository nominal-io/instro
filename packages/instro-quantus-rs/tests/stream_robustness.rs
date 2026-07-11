//! Regression tests for stream-engine failure modes found in adversarial
//! review: shutdown while the bounded event channel is full, idle sockets
//! outliving the read timeout, and triggered-type blocks poisoning packets.
//! Uses a hand-rolled in-test server (not the sim) so each scenario is exact.

use instro_quantus_rs::stream::{StreamEngine, StreamEvent};
use instro_quantus_rs::wire;
use std::io::Write;
use std::net::TcpListener;
use std::time::Duration;

fn packet_header(seq: u64, payload_size: u32) -> Vec<u8> {
    let mut b = Vec::with_capacity(wire::PACKET_HEADER_LEN);
    b.extend_from_slice(&seq.to_le_bytes());
    b.extend_from_slice(&1.25f64.to_le_bytes());
    b.extend_from_slice(&0.1f32.to_le_bytes());
    b.extend_from_slice(&payload_size.to_le_bytes());
    b.extend_from_slice(&wire::BYTE_ORDER_MARKER.to_le_bytes());
    b.extend_from_slice(&0u32.to_le_bytes());
    b
}

fn analog_block(channel_id: i32, samples: &[f32]) -> Vec<u8> {
    let mut b = Vec::new();
    b.extend_from_slice(&channel_id.to_le_bytes());
    b.extend_from_slice(&0i32.to_le_bytes()); // SampleType f32
    b.extend_from_slice(&0u32.to_le_bytes()); // ChannelType analog
    b.extend_from_slice(&((samples.len() * 4) as u32).to_le_bytes());
    b.extend_from_slice(&0u64.to_le_bytes()); // timestamp
    b.extend_from_slice(&[0u8; wire::ANALOG_HEADER_LEN_PROCESSED]);
    for s in samples {
        b.extend_from_slice(&s.to_le_bytes());
    }
    b
}

fn triggered_block(channel_id: i32, channel_type: u32, header_len: usize) -> Vec<u8> {
    let data = [0u8; 8];
    let mut b = Vec::new();
    b.extend_from_slice(&channel_id.to_le_bytes());
    b.extend_from_slice(&0i32.to_le_bytes());
    b.extend_from_slice(&channel_type.to_le_bytes());
    b.extend_from_slice(&(data.len() as u32).to_le_bytes());
    b.extend_from_slice(&0u64.to_le_bytes());
    b.extend_from_slice(&vec![0u8; header_len]);
    b.extend_from_slice(&data);
    b
}

fn packet(seq: u64, blocks: &[Vec<u8>]) -> Vec<u8> {
    let payload: Vec<u8> = blocks.concat();
    let mut b = packet_header(seq, payload.len() as u32);
    b.extend_from_slice(&payload);
    b
}

/// A slow-consumer shutdown must not deadlock: the reader thread parks in
/// tx.send once the 4096-event channel fills, and stop() must still return.
#[test]
fn stop_returns_while_reader_is_blocked_on_full_channel() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        let (mut conn, _) = listener.accept().unwrap();
        // Far more events than the channel holds; ignore write errors when
        // the client goes away.
        for seq in 0..6000u64 {
            let p = packet(seq, &[analog_block(1, &[0.5])]);
            if conn.write_all(&p).is_err() {
                return;
            }
        }
        // Keep the socket open so the reader is parked in send, not in read.
        std::thread::sleep(Duration::from_secs(20));
    });

    let engine = StreamEngine::connect("127.0.0.1", port).unwrap();
    // Wait for the channel to fill without consuming anything.
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    while engine.health().packets < 4097 && std::time::Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(engine.health().packets >= 4097, "channel never filled");

    let (done_tx, done_rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        engine.stop();
        let _ = done_tx.send(());
    });
    done_rx
        .recv_timeout(Duration::from_secs(5))
        .expect("stop() deadlocked with a full event channel");
    drop(server); // server thread exits on write error or its own sleep
}

/// A quiet-but-alive socket must not become a Disconnected event when the
/// read timeout elapses; data after the lull must still be delivered.
#[test]
fn idle_periods_longer_than_the_read_timeout_are_not_disconnects() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        let (mut conn, _) = listener.accept().unwrap();
        conn.write_all(&packet(0, &[analog_block(1, &[1.0])]))
            .unwrap();
        std::thread::sleep(Duration::from_millis(900)); // >> 200ms timeout
        conn.write_all(&packet(1, &[analog_block(1, &[2.0])]))
            .unwrap();
        std::thread::sleep(Duration::from_millis(500));
    });

    let engine =
        StreamEngine::connect_with_read_timeout("127.0.0.1", port, Duration::from_millis(200))
            .unwrap();
    let mut analog = 0;
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    while analog < 2 && std::time::Instant::now() < deadline {
        match engine.events().recv_timeout(Duration::from_millis(200)) {
            Ok(StreamEvent::Analog(_)) => analog += 1,
            Ok(StreamEvent::Disconnected { reason }) => {
                panic!("idle socket surfaced as disconnect: {reason}")
            }
            _ => {}
        }
    }
    assert_eq!(analog, 2, "sample after the idle period was not delivered");
    engine.stop();
    server.join().unwrap();
}

/// Triggered data/scope blocks (types 4/5) are skippable: channels after them
/// in the same packet must still parse.
#[test]
fn triggered_blocks_do_not_poison_the_rest_of_the_packet() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        let (mut conn, _) = listener.accept().unwrap();
        let blocks = vec![
            triggered_block(7, 4, wire::TRIGGERED_DATA_HEADER_LEN),
            triggered_block(8, 5, wire::TRIGGERED_SCOPE_HEADER_LEN),
            analog_block(1, &[0.25, 0.5]),
        ];
        conn.write_all(&packet(0, &blocks)).unwrap();
        std::thread::sleep(Duration::from_millis(500));
    });

    let engine = StreamEngine::connect("127.0.0.1", port).unwrap();
    let mut skipped = Vec::new();
    let mut analog_samples = Vec::new();
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    while analog_samples.is_empty() && std::time::Instant::now() < deadline {
        match engine.events().recv_timeout(Duration::from_millis(200)) {
            Ok(StreamEvent::Skipped { channel_type, .. }) => skipped.push(channel_type),
            Ok(StreamEvent::Analog(batch)) => analog_samples = batch.samples,
            _ => {}
        }
    }
    assert_eq!(skipped, vec![4, 5]);
    assert_eq!(analog_samples, vec![0.25, 0.5]);
    engine.stop();
    server.join().unwrap();
}
