//! Hand-built golden packets, assembled byte-by-byte from the manual's layout
//! tables (docs/api-notes.md section 5).
//!
//! These deliberately do NOT use quantus-client's parser types or the
//! simulator's encoder (PLAN.md D7): they are the spec-as-written, used to
//! referee both implementations. Until real-hardware captures land in
//! fixtures/golden/, these are the ground truth. The tests here only assert
//! internal consistency (sizes, offsets, marker placement); parser round-trip
//! tests are added in Phase 3.

use instro_quantus::wire;

fn le_u32(v: u32) -> [u8; 4] {
    v.to_le_bytes()
}

/// Packet header: u64 seq, f64 transmit_ts, f32 buffer_level, u32 payload_size,
/// u32 BOM (0xFFFE), u32 payload_type.
fn packet_header(seq: u64, transmit_ts: f64, buffer_level: f32, payload_size: u32) -> Vec<u8> {
    let mut b = Vec::with_capacity(wire::PACKET_HEADER_LEN);
    b.extend_from_slice(&seq.to_le_bytes());
    b.extend_from_slice(&transmit_ts.to_le_bytes());
    b.extend_from_slice(&buffer_level.to_le_bytes());
    b.extend_from_slice(&le_u32(payload_size));
    b.extend_from_slice(&le_u32(wire::BYTE_ORDER_MARKER));
    b.extend_from_slice(&le_u32(0)); // PayloadType::Data
    b
}

/// Generic channel header: i32 channel_id, i32 sample_type, u32 channel_type,
/// u32 channel_data_size, u64 timestamp_ns.
fn generic_channel_header(
    channel_id: i32,
    sample_type: i32,
    channel_type: u32,
    channel_data_size: u32,
    timestamp_ns: u64,
) -> Vec<u8> {
    let mut b = Vec::with_capacity(wire::GENERIC_CHANNEL_HEADER_LEN);
    b.extend_from_slice(&channel_id.to_le_bytes());
    b.extend_from_slice(&sample_type.to_le_bytes());
    b.extend_from_slice(&le_u32(channel_type));
    b.extend_from_slice(&le_u32(channel_data_size));
    b.extend_from_slice(&timestamp_ns.to_le_bytes());
    b
}

/// Analog specific header, Processed mode: i32 integrity, i32 level_crossing,
/// f32 level, f32 min, f32 max.
fn analog_header_processed(integrity: i32, level: f32, min: f32, max: f32) -> Vec<u8> {
    let mut b = Vec::with_capacity(wire::ANALOG_HEADER_LEN_PROCESSED);
    b.extend_from_slice(&integrity.to_le_bytes());
    b.extend_from_slice(&0i32.to_le_bytes()); // LevelCrossingOccurred (unsupported)
    b.extend_from_slice(&level.to_le_bytes());
    b.extend_from_slice(&min.to_le_bytes());
    b.extend_from_slice(&max.to_le_bytes());
    b
}

/// One analog channel block in Processed (f32) mode.
fn analog_block_processed(channel_id: i32, timestamp_ns: u64, samples: &[f32]) -> Vec<u8> {
    // ChannelDataSize covers ONLY the sample bytes (vendor StreamData.py reads
    // the 20-byte specific header separately, then channel_data_size bytes).
    let data_size = (samples.len() * 4) as u32;
    let mut b = generic_channel_header(channel_id, 0, 0, data_size, timestamp_ns);
    b.extend_from_slice(&analog_header_processed(0, 0.5, -1.0, 1.0));
    for s in samples {
        b.extend_from_slice(&s.to_le_bytes());
    }
    b
}

/// One tacho channel block: f64 event timestamps (ms from epoch start), no
/// specific header.
fn tacho_block(channel_id: i32, timestamp_ns: u64, events_ms: &[f64]) -> Vec<u8> {
    let data_size = (events_ms.len() * 8) as u32;
    let mut b = generic_channel_header(channel_id, 0, 1, data_size, timestamp_ns);
    for e in events_ms {
        b.extend_from_slice(&e.to_le_bytes());
    }
    b
}

/// One CAN message: f64 timestamp (s), u32 id, u8 header, u8 frame_format,
/// u8 frame_type, u8 dlc, then data bytes.
fn can_message(timestamp_s: f64, id: u32, dlc: u8, data: &[u8]) -> Vec<u8> {
    let mut b = Vec::with_capacity(wire::CAN_MESSAGE_PREFIX_LEN + data.len());
    b.extend_from_slice(&timestamp_s.to_le_bytes());
    b.extend_from_slice(&id.to_le_bytes());
    b.push(0); // header
    b.push(0); // frame format
    b.push(0); // frame type
    b.push(dlc);
    b.extend_from_slice(data);
    b
}

/// One CAN channel block: 24-byte reserved header, then messages.
/// ChannelDataSize covers only the message bytes, not the reserved header.
fn can_block(channel_id: i32, timestamp_ns: u64, messages: &[Vec<u8>]) -> Vec<u8> {
    let msg_len: usize = messages.iter().map(Vec::len).sum();
    let data_size = msg_len as u32;
    let mut b = generic_channel_header(channel_id, 0, 2, data_size, timestamp_ns);
    b.extend_from_slice(&[0u8; wire::CAN_RESERVED_HEADER_LEN]);
    for m in messages {
        b.extend_from_slice(m);
    }
    b
}

/// Full packet: header + channel blocks, with PayloadSize computed.
fn packet(seq: u64, blocks: &[Vec<u8>]) -> Vec<u8> {
    let payload: Vec<u8> = blocks.concat();
    let mut b = packet_header(seq, 1.25, 0.10, payload.len() as u32);
    b.extend_from_slice(&payload);
    b
}

#[test]
fn packet_header_layout() {
    let h = packet_header(7, 1.25, 0.10, 128);
    assert_eq!(h.len(), wire::PACKET_HEADER_LEN);
    assert_eq!(u64::from_le_bytes(h[0..8].try_into().unwrap()), 7);
    // BOM sits at offset 24, after seq(8) + ts(8) + buffer_level(4) + payload_size(4).
    assert_eq!(
        u32::from_le_bytes(h[24..28].try_into().unwrap()),
        wire::BYTE_ORDER_MARKER
    );
    assert_eq!(u32::from_le_bytes(h[20..24].try_into().unwrap()), 128);
}

#[test]
fn analog_processed_block_layout() {
    let samples = [0.0f32, 0.1, -0.1, 0.25];
    let b = analog_block_processed(11, 1_000_000_000, &samples);
    assert_eq!(
        b.len(),
        wire::GENERIC_CHANNEL_HEADER_LEN + wire::ANALOG_HEADER_LEN_PROCESSED + samples.len() * 4
    );
    // ChannelDataSize (offset 12) covers only the sample bytes.
    assert_eq!(
        u32::from_le_bytes(b[12..16].try_into().unwrap()) as usize,
        samples.len() * 4
    );
    // Timestamp at offset 16.
    assert_eq!(
        u64::from_le_bytes(b[16..24].try_into().unwrap()),
        1_000_000_000
    );
    // First sample immediately after generic + specific headers.
    let first = wire::GENERIC_CHANNEL_HEADER_LEN + wire::ANALOG_HEADER_LEN_PROCESSED;
    assert_eq!(
        f32::from_le_bytes(b[first..first + 4].try_into().unwrap()),
        0.0
    );
}

#[test]
fn tacho_block_layout() {
    let events = [10.0f64, 20.5, 31.2];
    let b = tacho_block(23, 0, &events);
    assert_eq!(b.len(), wire::GENERIC_CHANNEL_HEADER_LEN + events.len() * 8);
    assert_eq!(u32::from_le_bytes(b[8..12].try_into().unwrap()), 1); // ChannelType::Tacho
}

#[test]
fn can_block_layout() {
    let msgs = vec![
        can_message(0.010, 0x18FF50E5, 8, &[1, 2, 3, 4, 5, 6, 7, 8]),
        can_message(0.020, 0x123, 3, &[9, 10, 11]),
    ];
    let b = can_block(41, 0, &msgs);
    let expected_msgs: usize = msgs.iter().map(Vec::len).sum();
    assert_eq!(
        b.len(),
        wire::GENERIC_CHANNEL_HEADER_LEN + wire::CAN_RESERVED_HEADER_LEN + expected_msgs
    );
    assert_eq!(
        u32::from_le_bytes(b[12..16].try_into().unwrap()) as usize,
        expected_msgs
    );
    assert_eq!(
        msgs[0].len(),
        wire::CAN_MESSAGE_PREFIX_LEN + 8 // prefix + DLC data bytes
    );
    assert_eq!(u32::from_le_bytes(b[8..12].try_into().unwrap()), 2); // ChannelType::Can
}

#[test]
fn multi_channel_packet_is_internally_consistent() {
    let blocks = vec![
        analog_block_processed(11, 1_000_000_000, &[0.1, 0.2]),
        tacho_block(23, 1_000_000_000, &[5.0]),
        can_block(
            41,
            1_000_000_000,
            &[can_message(0.001, 0x100, 2, &[0xAA, 0xBB])],
        ),
    ];
    let p = packet(1, &blocks);
    let payload_size = u32::from_le_bytes(p[20..24].try_into().unwrap()) as usize;
    assert_eq!(p.len(), wire::PACKET_HEADER_LEN + payload_size);

    // Walk the payload block-by-block: generic header + type-specific header
    // length + ChannelDataSize (sample/message bytes only).
    let mut off = wire::PACKET_HEADER_LEN;
    let mut ids = Vec::new();
    while off < p.len() {
        ids.push(i32::from_le_bytes(p[off..off + 4].try_into().unwrap()));
        let channel_type = u32::from_le_bytes(p[off + 8..off + 12].try_into().unwrap());
        let data_size = u32::from_le_bytes(p[off + 12..off + 16].try_into().unwrap()) as usize;
        let specific_len = match channel_type {
            0 => wire::ANALOG_HEADER_LEN_PROCESSED,
            1 => 0,
            2 => wire::CAN_RESERVED_HEADER_LEN,
            3 => wire::GPS_HEADER_LEN,
            other => panic!("unexpected channel type {other}"),
        };
        off += wire::GENERIC_CHANNEL_HEADER_LEN + specific_len + data_size;
    }
    assert_eq!(off, p.len());
    assert_eq!(ids, vec![11, 23, 41]);
}
