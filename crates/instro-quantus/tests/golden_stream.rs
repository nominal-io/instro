//! Parse the real-hardware stream capture (fixtures/golden/microq_20260722)
//! through the actual StreamEngine, served over a local TCP socket.

use instro_quantus::stream::{StreamEngine, StreamEvent};
use std::collections::BTreeMap;
use std::io::Write;
use std::net::TcpListener;
use std::time::Duration;

#[test]
fn parses_microq_hardware_capture() {
    let capture = std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/golden/microq_20260722/stream.bin"
    ))
    .unwrap();

    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        socket.write_all(&capture).unwrap();
        // Dropping the socket EOFs the reader mid-packet (the capture's
        // truncated tail), which must surface as a disconnect, not a panic.
    });

    let engine = StreamEngine::connect("127.0.0.1", port).unwrap();
    let mut analog: BTreeMap<i32, u64> = BTreeMap::new();
    let mut analog_samples = 0usize;
    let mut skipped: BTreeMap<(i32, u32), u64> = BTreeMap::new();
    let mut gaps = 0u64;
    let mut others = 0u64;
    loop {
        match engine.events().recv_timeout(Duration::from_secs(30)) {
            Ok(StreamEvent::Analog(batch)) => {
                *analog.entry(batch.channel_id).or_default() += 1;
                analog_samples += batch.samples.len();
            }
            Ok(StreamEvent::Skipped {
                channel_id,
                channel_type,
            }) => *skipped.entry((channel_id, channel_type)).or_default() += 1,
            Ok(StreamEvent::Gap { .. }) => gaps += 1,
            Ok(StreamEvent::Disconnected { .. }) => break,
            Ok(_) => others += 1,
            Err(e) => panic!("stream ended without a disconnect event: {e}"),
        }
    }
    server.join().unwrap();

    let expected: BTreeMap<i32, u64> = [9, 10, 11, 12, 15, 17]
        .into_iter()
        .map(|id| (id, 1279))
        .collect();
    assert_eq!(analog, expected);
    assert!(analog_samples > 0);
    // 18 GPS blocks on channel item 4 (channel type 3), cleanly skipped.
    assert_eq!(skipped, BTreeMap::from([((4, 3), 18)]));
    assert_eq!(gaps, 0, "contiguous capture must produce no gap events");
    assert_eq!(others, 0, "no tacho/CAN/epoch events in this capture");

    let health = engine.health();
    assert_eq!(health.packets, 1284);
    assert_eq!(health.gaps, 0);
    assert_eq!(health.missing_packets, 0);
    engine.stop();
}
