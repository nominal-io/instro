//! Stream engine: a dedicated reader thread that drains the device's binary
//! TCP stream, parses packets, tracks the streaming epoch (sequence resets)
//! and gaps (server-side discards), and hands batches to the consumer through
//! a bounded channel.

use crate::error::{Error, Result};
use crate::wire;
use std::io::Read;
use std::net::TcpStream;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, mpsc};
use std::time::Duration;

#[derive(Debug, Clone)]
pub enum StreamEvent {
    Analog(AnalogBatch),
    /// f64 event timestamps (ms from epoch start) from a tacho channel.
    Tacho {
        channel_id: i32,
        events_ms: Vec<f64>,
        received_unix_ns: u64,
    },
    /// Raw timestamped CAN frames; DBC signal decoding happens in `dbc`
    /// (PLAN.md D14) at the consumer's discretion.
    Can {
        channel_id: i32,
        frames: Vec<CanFrame>,
        received_unix_ns: u64,
    },
    /// Sequence numbers restarted: a new streaming epoch (settings applied or
    /// device restarted). Timestamps rebase from zero.
    EpochRestart {
        sequence: u64,
        received_unix_ns: u64,
    },
    /// The server discarded `missing` packets (buffer overrun or suspend).
    Gap {
        missing: u64,
        received_unix_ns: u64,
    },
    /// A channel type this engine doesn't decode yet (GPS/triggered).
    Skipped {
        channel_id: i32,
        channel_type: u32,
    },
    Disconnected {
        reason: String,
    },
}

#[derive(Debug, Clone)]
pub struct CanFrame {
    /// Seconds from epoch start (the device timestamps frames on the same
    /// clock as the analog channels).
    pub timestamp_s: f64,
    /// Arbitration id.
    pub id: u32,
    pub header: u8,
    pub frame_format: u8,
    pub frame_type: u8,
    pub data: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct AnalogBatch {
    pub channel_id: i32,
    /// First-sample timestamp in ns (epoch-relative, or PTP epoch when active).
    pub timestamp_ns: u64,
    pub integrity: i32,
    pub min: f32,
    pub max: f32,
    pub samples: Vec<f32>,
    /// Host wall-clock time (Unix ns) when the carrying packet was read off
    /// the socket. Consumers anchor stream-relative timestamps from this, not
    /// from their own (possibly backlogged) processing time.
    pub received_unix_ns: u64,
}

#[derive(Debug, Clone, Default)]
pub struct HealthSnapshot {
    pub packets: u64,
    pub gaps: u64,
    pub missing_packets: u64,
    pub epoch_restarts: u64,
    /// Server-reported buffer fill (0..1) from the latest packet header.
    pub buffer_level: f32,
    /// Device TransmitTimestamp (s) from the latest packet header; recorded so
    /// hardware validation can measure device-to-host clock offset/latency.
    pub transmit_timestamp_s: f64,
}

#[derive(Default)]
struct Health {
    packets: AtomicU64,
    gaps: AtomicU64,
    missing: AtomicU64,
    restarts: AtomicU64,
    buffer_level_bits: AtomicU32,
    transmit_ts_bits: AtomicU64,
}

pub struct StreamEngine {
    /// `Option` so shutdown can drop the receiver BEFORE joining the reader
    /// thread: a reader parked in a full `tx.send` only unblocks when the
    /// receiver goes away (socket shutdown does not reach it).
    events: Option<mpsc::Receiver<StreamEvent>>,
    health: Arc<Health>,
    socket: TcpStream,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl StreamEngine {
    pub fn connect(host: &str, port: u16) -> Result<Self> {
        Self::connect_with_read_timeout(host, port, Duration::from_secs(10))
    }

    /// `connect` with an explicit socket read timeout (exposed for tests that
    /// exercise idle behavior without waiting the production 10s).
    pub fn connect_with_read_timeout(host: &str, port: u16, timeout: Duration) -> Result<Self> {
        let socket = TcpStream::connect((host, port))
            .map_err(|e| Error::Transport(format!("stream connect: {e}")))?;
        // The timeout exists only so a blocked read wakes periodically;
        // read_full treats it as idle, never as disconnect (the protocol has
        // no keepalive and quiet periods are normal).
        socket
            .set_read_timeout(Some(timeout))
            .map_err(|e| Error::Transport(e.to_string()))?;
        let reader = socket
            .try_clone()
            .map_err(|e| Error::Transport(e.to_string()))?;

        let (tx, rx) = mpsc::sync_channel::<StreamEvent>(4096);
        let health = Arc::new(Health::default());
        let thread_health = health.clone();
        let handle = std::thread::spawn(move || read_loop(reader, tx, &thread_health));

        Ok(StreamEngine {
            events: Some(rx),
            health,
            socket,
            handle: Some(handle),
        })
    }

    /// Blocking event receiver; disconnect is delivered as an event.
    pub fn events(&self) -> &mpsc::Receiver<StreamEvent> {
        self.events
            .as_ref()
            .expect("receiver taken only at shutdown")
    }

    pub fn health(&self) -> HealthSnapshot {
        HealthSnapshot {
            packets: self.health.packets.load(Ordering::Relaxed),
            gaps: self.health.gaps.load(Ordering::Relaxed),
            missing_packets: self.health.missing.load(Ordering::Relaxed),
            epoch_restarts: self.health.restarts.load(Ordering::Relaxed),
            buffer_level: f32::from_bits(self.health.buffer_level_bits.load(Ordering::Relaxed)),
            transmit_timestamp_s: f64::from_bits(
                self.health.transmit_ts_bits.load(Ordering::Relaxed),
            ),
        }
    }

    pub fn stop(mut self) {
        self.shutdown();
    }

    /// Unblock the reader wherever it is parked (socket read OR full-channel
    /// send), then join it. Order matters: drop the receiver first.
    fn shutdown(&mut self) {
        let _ = self.socket.shutdown(std::net::Shutdown::Both);
        drop(self.events.take());
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }
}

impl Drop for StreamEngine {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn unix_now_ns() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}

/// `read_exact` that treats read timeouts as idle (retrying from where it left
/// off) instead of as errors. Returns Err only on EOF or a real I/O error.
fn read_full(socket: &mut TcpStream, buf: &mut [u8]) -> std::io::Result<()> {
    let mut filled = 0usize;
    while filled < buf.len() {
        match socket.read(&mut buf[filled..]) {
            Ok(0) => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::UnexpectedEof,
                    "connection closed",
                ));
            }
            Ok(n) => filled += n,
            Err(e)
                if e.kind() == std::io::ErrorKind::WouldBlock
                    || e.kind() == std::io::ErrorKind::TimedOut => {}
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e),
        }
    }
    Ok(())
}

fn read_loop(mut socket: TcpStream, tx: mpsc::SyncSender<StreamEvent>, health: &Health) {
    let mut last_sequence: Option<u64> = None;
    loop {
        let mut header = [0u8; wire::PACKET_HEADER_LEN];
        if let Err(e) = read_full(&mut socket, &mut header) {
            let _ = tx.send(StreamEvent::Disconnected {
                reason: e.to_string(),
            });
            return;
        }
        let received_unix_ns = unix_now_ns();
        let sequence = u64::from_le_bytes(header[0..8].try_into().unwrap());
        let transmit_ts = f64::from_le_bytes(header[8..16].try_into().unwrap());
        let buffer_level = f32::from_le_bytes(header[16..20].try_into().unwrap());
        let payload_size = u32::from_le_bytes(header[20..24].try_into().unwrap()) as usize;
        let marker = u32::from_le_bytes(header[24..28].try_into().unwrap());
        let payload_type = u32::from_le_bytes(header[28..32].try_into().unwrap());

        if marker != wire::BYTE_ORDER_MARKER {
            let _ = tx.send(StreamEvent::Disconnected {
                reason: format!("bad byte order marker 0x{marker:X}"),
            });
            return;
        }
        if payload_size > wire::MAX_PAYLOAD_SIZE {
            let _ = tx.send(StreamEvent::Disconnected {
                reason: format!("implausible payload size {payload_size} (corrupt header)"),
            });
            return;
        }

        let mut payload = vec![0u8; payload_size];
        if let Err(e) = read_full(&mut socket, &mut payload) {
            let _ = tx.send(StreamEvent::Disconnected {
                reason: e.to_string(),
            });
            return;
        }

        health.packets.fetch_add(1, Ordering::Relaxed);
        health
            .buffer_level_bits
            .store(buffer_level.to_bits(), Ordering::Relaxed);
        health
            .transmit_ts_bits
            .store(transmit_ts.to_bits(), Ordering::Relaxed);

        match last_sequence {
            Some(last) if sequence < last => {
                health.restarts.fetch_add(1, Ordering::Relaxed);
                if tx
                    .send(StreamEvent::EpochRestart {
                        sequence,
                        received_unix_ns,
                    })
                    .is_err()
                {
                    return;
                }
            }
            Some(last) if sequence > last + 1 => {
                let missing = sequence - last - 1;
                health.gaps.fetch_add(1, Ordering::Relaxed);
                health.missing.fetch_add(missing, Ordering::Relaxed);
                if tx
                    .send(StreamEvent::Gap {
                        missing,
                        received_unix_ns,
                    })
                    .is_err()
                {
                    return;
                }
            }
            _ => {}
        }
        last_sequence = Some(sequence);

        if payload_type != 0 {
            continue;
        }
        if parse_payload(&payload, received_unix_ns, &tx).is_err() {
            return; // consumer hung up
        }
    }
}

fn parse_payload(
    payload: &[u8],
    received_unix_ns: u64,
    tx: &mpsc::SyncSender<StreamEvent>,
) -> std::result::Result<(), ()> {
    let mut index = 0usize;
    while index + wire::GENERIC_CHANNEL_HEADER_LEN <= payload.len() {
        let header = &payload[index..index + wire::GENERIC_CHANNEL_HEADER_LEN];
        index += wire::GENERIC_CHANNEL_HEADER_LEN;
        let channel_id = i32::from_le_bytes(header[0..4].try_into().unwrap());
        let sample_type = i32::from_le_bytes(header[4..8].try_into().unwrap());
        let channel_type = u32::from_le_bytes(header[8..12].try_into().unwrap());
        let data_size = u32::from_le_bytes(header[12..16].try_into().unwrap()) as usize;
        let timestamp_ns = u64::from_le_bytes(header[16..24].try_into().unwrap());

        match channel_type {
            0 => {
                if index + wire::ANALOG_HEADER_LEN_PROCESSED > payload.len() {
                    return Ok(());
                }
                let specific = &payload[index..index + wire::ANALOG_HEADER_LEN_PROCESSED];
                index += wire::ANALOG_HEADER_LEN_PROCESSED;
                let integrity = i32::from_le_bytes(specific[0..4].try_into().unwrap());
                let min = f32::from_le_bytes(specific[12..16].try_into().unwrap());
                let max = f32::from_le_bytes(specific[16..20].try_into().unwrap());

                // Raw modes carry an extra f32 scaling factor before the data.
                let scaling = if sample_type != 0 {
                    if index + 4 > payload.len() {
                        return Ok(());
                    }
                    let s = f32::from_le_bytes(payload[index..index + 4].try_into().unwrap());
                    index += 4;
                    Some(s)
                } else {
                    None
                };
                if index + data_size > payload.len() {
                    return Ok(());
                }
                let data = &payload[index..index + data_size];
                index += data_size;
                let samples = decode_analog_samples(sample_type, scaling, data);

                tx.send(StreamEvent::Analog(AnalogBatch {
                    channel_id,
                    timestamp_ns,
                    integrity,
                    min,
                    max,
                    samples,
                    received_unix_ns,
                }))
                .map_err(|_| ())?;
            }
            1 => {
                if index + data_size > payload.len() {
                    return Ok(());
                }
                let events_ms = payload[index..index + data_size]
                    .chunks_exact(8)
                    .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
                    .collect();
                index += data_size;
                tx.send(StreamEvent::Tacho {
                    channel_id,
                    events_ms,
                    received_unix_ns,
                })
                .map_err(|_| ())?;
            }
            2 => {
                if index + wire::CAN_RESERVED_HEADER_LEN + data_size > payload.len() {
                    return Ok(());
                }
                index += wire::CAN_RESERVED_HEADER_LEN;
                let frames = parse_can_messages(&payload[index..index + data_size]);
                index += data_size;
                tx.send(StreamEvent::Can {
                    channel_id,
                    frames,
                    received_unix_ns,
                })
                .map_err(|_| ())?;
            }
            3 => {
                index += wire::GPS_HEADER_LEN + data_size;
                tx.send(StreamEvent::Skipped {
                    channel_id,
                    channel_type,
                })
                .map_err(|_| ())?;
            }
            // Triggered data/scope blocks: known specific-header sizes, so
            // they are cleanly skippable (a previous vendor-software session
            // can leave such channels streaming; they must not poison the
            // rest of the packet).
            4 => {
                index += wire::TRIGGERED_DATA_HEADER_LEN + data_size;
                tx.send(StreamEvent::Skipped {
                    channel_id,
                    channel_type,
                })
                .map_err(|_| ())?;
            }
            5 => {
                index += wire::TRIGGERED_SCOPE_HEADER_LEN + data_size;
                tx.send(StreamEvent::Skipped {
                    channel_id,
                    channel_type,
                })
                .map_err(|_| ())?;
            }
            _ => {
                // Unknown type: cannot know its specific-header size; stop
                // parsing this payload rather than misalign.
                tx.send(StreamEvent::Skipped {
                    channel_id,
                    channel_type,
                })
                .map_err(|_| ())?;
                return Ok(());
            }
        }
    }
    Ok(())
}

/// Parse the message run of a CAN block. The wire DLC field carries the actual
/// payload byte count (the vendor parser reads `data[index:index+dlc]`), not
/// the CAN-FD DLC code table.
fn parse_can_messages(data: &[u8]) -> Vec<CanFrame> {
    let mut frames = Vec::new();
    let mut index = 0usize;
    while index + wire::CAN_MESSAGE_PREFIX_LEN <= data.len() {
        let timestamp_s = f64::from_le_bytes(data[index..index + 8].try_into().unwrap());
        let id = u32::from_le_bytes(data[index + 8..index + 12].try_into().unwrap());
        let header = data[index + 12];
        let frame_format = data[index + 13];
        let frame_type = data[index + 14];
        let dlc = data[index + 15] as usize;
        index += wire::CAN_MESSAGE_PREFIX_LEN;
        if index + dlc > data.len() {
            break;
        }
        frames.push(CanFrame {
            timestamp_s,
            id,
            header,
            frame_format,
            frame_type,
            data: data[index..index + dlc].to_vec(),
        });
        index += dlc;
    }
    frames
}

fn decode_analog_samples(sample_type: i32, scaling: Option<f32>, data: &[u8]) -> Vec<f32> {
    let scale = scaling.unwrap_or(1.0);
    match sample_type {
        0 => data
            .chunks_exact(4)
            .map(|c| f32::from_le_bytes(c.try_into().unwrap()))
            .collect(),
        1 => data
            .chunks_exact(2)
            .map(|c| scale * f32::from(i16::from_le_bytes(c.try_into().unwrap())))
            .collect(),
        2 => data
            .chunks_exact(3)
            .map(|c| {
                // 24-bit little-endian signed, assembled into the top bytes of
                // an i32 (matching the vendor example) then scaled.
                let raw = ((c[2] as i32) << 24) | ((c[1] as i32) << 16) | ((c[0] as i32) << 8);
                scale * raw as f32
            })
            .collect(),
        3 => data
            .chunks_exact(4)
            .map(|c| scale * i32::from_le_bytes(c.try_into().unwrap()) as f32)
            .collect(),
        _ => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_raw16_with_scaling() {
        let data = [0x00u8, 0x40, 0x00, 0xC0]; // 16384, -16384
        let samples = decode_analog_samples(1, Some(0.5), &data);
        assert_eq!(samples, vec![8192.0, -8192.0]);
    }

    #[test]
    fn decodes_raw24_sign_extension() {
        // 0xFFFFFF as 24-bit = -1 -> assembled as -1 << 8 = -256.
        let samples = decode_analog_samples(2, Some(1.0), &[0xFF, 0xFF, 0xFF]);
        assert_eq!(samples, vec![-256.0]);
    }

    #[test]
    fn decodes_f32_ignoring_scaling() {
        let data = 1.5f32.to_le_bytes();
        let samples = decode_analog_samples(0, None, &data);
        assert_eq!(samples, vec![1.5]);
    }
}
