//! Binary stream plane: a paced generator that emits QServer data packets for
//! every streaming-enabled analog channel, with a bounded server-side buffer
//! that discards above 45% (like the real device) and fault-injection knobs.
//!
//! The wire ENCODER here is written from the manual's byte layout tables
//! independently of quantus-client's parser (PLAN.md D7) — do not import
//! quantus-client.

use crate::config::{SignalConfig, SimConfig};
use crate::model::SimState;
use serde_json::Value;
use std::io::Write;
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::time::{Duration, Instant};

/// Generator tick; small enough that vendor examples reading 500 packets
/// finish quickly.
const TICK: Duration = Duration::from_millis(20);
const DISCARD_LEVEL: f32 = 0.45;

/// State shared between the REST plane and the stream generator.
pub struct StreamShared {
    pub suspended: AtomicBool,
}

impl StreamShared {
    pub fn new() -> Self {
        StreamShared {
            suspended: AtomicBool::new(false),
        }
    }
}

impl Default for StreamShared {
    fn default() -> Self {
        Self::new()
    }
}

pub fn spawn_stream_server(
    listener: TcpListener,
    state: Arc<Mutex<SimState>>,
    config: SimConfig,
    shared: Arc<StreamShared>,
    stop: Arc<AtomicBool>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        listener
            .set_nonblocking(true)
            .expect("nonblocking listener");
        let client_active = Arc::new(AtomicBool::new(false));
        while !stop.load(Ordering::Relaxed) {
            match listener.accept() {
                Ok((socket, _)) => {
                    if client_active.swap(true, Ordering::SeqCst) {
                        // Only one streaming client allowed: reject by closing.
                        drop(socket);
                        client_active.store(true, Ordering::SeqCst);
                        continue;
                    }
                    let state = state.clone();
                    let config = config.clone();
                    let shared = shared.clone();
                    let stop = stop.clone();
                    let active = client_active.clone();
                    std::thread::spawn(move || {
                        let _ = socket.set_nodelay(true);
                        serve_connection(socket, &state, &config, &shared, &stop);
                        active.store(false, Ordering::SeqCst);
                    });
                }
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(_) => break,
            }
        }
    })
}

/// Channel ItemNameIdentifiers that stream tacho events / CAN frames
/// (QProtocolCSharp ChannelType values; docs/api-notes.md section 9).
const TACHO_CHANNEL_IDENTIFIERS: [i64; 3] = [16, 17, 23];
const CAN_CHANNEL_IDENTIFIERS: [i64; 3] = [0, 34, 44];
/// XMC237 GPS channel (identifier 36): stream format unverified, never emitted.
const GPS_CHANNEL_IDENTIFIER: i64 = 36;

/// Full scale assumed for Raw-mode scaling (A18: real per-module FS unknown).
const RAW_FULL_SCALE: f32 = 10.0;

enum ChannelKind {
    Analog {
        signal: SignalConfig,
        raw: bool,
    },
    Tacho {
        rpm: f64,
    },
    Can {
        playback: Vec<crate::config::CanPlayback>,
        sent: Vec<u64>,
    },
}

struct ActiveChannel {
    item_id: i64,
    rate_hz: f64,
    kind: ChannelKind,
    emitted: u64,
}

fn serve_connection(
    socket: TcpStream,
    state: &Mutex<SimState>,
    config: &SimConfig,
    shared: &StreamShared,
    stop: &AtomicBool,
) {
    let faults = &config.faults;
    let capacity = faults.stream_buffer_packets.max(2);
    let (tx, rx) = mpsc::sync_channel::<Vec<u8>>(capacity);
    let pending = Arc::new(AtomicUsize::new(0));
    let writer_done = Arc::new(AtomicBool::new(false));

    let writer_pending = pending.clone();
    let writer_flag = writer_done.clone();
    let mut writer_socket = socket;
    let writer = std::thread::spawn(move || {
        while let Ok(packet) = rx.recv() {
            writer_pending.fetch_sub(1, Ordering::SeqCst);
            if writer_socket.write_all(&packet).is_err() {
                break;
            }
        }
        writer_flag.store(true, Ordering::SeqCst);
        let _ = writer_socket.shutdown(std::net::Shutdown::Both);
    });

    let mut sequence: u64 = 0;
    let mut packet_counter: u64 = 0;
    let mut local_epoch = state.lock().unwrap().epoch;
    let mut epoch_start = Instant::now();
    let mut channels = active_channels(&state.lock().unwrap(), config);
    let started = Instant::now();

    'outer: while !stop.load(Ordering::Relaxed) && !writer_done.load(Ordering::SeqCst) {
        std::thread::sleep(TICK);

        {
            let state = state.lock().unwrap();
            if state.epoch != local_epoch {
                local_epoch = state.epoch;
                sequence = 0;
                epoch_start = Instant::now();
                channels = active_channels(&state, config);
            }
        }

        let elapsed = epoch_start.elapsed().as_secs_f64();
        let mut blocks: Vec<Vec<u8>> = Vec::new();
        // CAN blocks go LAST: Mecalc's own StreamData.py example over-advances
        // its index after a CAN block, so trailing placement keeps the vendor
        // referee parser working.
        let mut can_blocks: Vec<Vec<u8>> = Vec::new();
        for channel in &mut channels {
            let item_id = channel.item_id as i32;
            match &mut channel.kind {
                ChannelKind::Analog { signal, raw } => {
                    let target = (elapsed * channel.rate_hz) as u64;
                    let count = target.saturating_sub(channel.emitted);
                    if count == 0 {
                        continue;
                    }
                    let t0 = channel.emitted as f64 / channel.rate_hz;
                    let samples: Vec<f32> = (0..count)
                        .map(|i| signal.value_at(t0 + i as f64 / channel.rate_hz))
                        .collect();
                    channel.emitted += count;
                    let t0_ns = (t0 * 1e9) as u64;
                    blocks.push(if *raw {
                        analog_block_raw24(item_id, t0_ns, &samples)
                    } else {
                        analog_block_f32(item_id, t0_ns, &samples)
                    });
                }
                ChannelKind::Tacho { rpm } => {
                    let edge_rate = *rpm / 60.0; // one pulse per revolution
                    let target = (elapsed * edge_rate) as u64;
                    let count = target.saturating_sub(channel.emitted);
                    if count == 0 {
                        continue;
                    }
                    let events_ms: Vec<f64> = (0..count)
                        .map(|i| (channel.emitted + i) as f64 / edge_rate * 1e3)
                        .collect();
                    channel.emitted += count;
                    blocks.push(tacho_block(item_id, &events_ms));
                }
                ChannelKind::Can { playback, sent } => {
                    let mut messages: Vec<Vec<u8>> = Vec::new();
                    for (entry, sent_count) in playback.iter().zip(sent.iter_mut()) {
                        let target = (elapsed * 1e3 / entry.period_ms as f64) as u64;
                        while *sent_count < target {
                            let timestamp_s = *sent_count as f64 * entry.period_ms as f64 / 1e3;
                            messages.push(can_message(
                                timestamp_s,
                                entry.id,
                                entry.dlc,
                                *sent_count,
                            ));
                            *sent_count += 1;
                        }
                    }
                    if !messages.is_empty() {
                        can_blocks.push(can_block(item_id, &messages));
                    }
                }
            }
        }
        blocks.append(&mut can_blocks);
        if blocks.is_empty() {
            continue;
        }

        // Suspended: data is produced and discarded, leaving sequence gaps.
        let suspended = shared.suspended.load(Ordering::Relaxed);
        let level = pending.load(Ordering::SeqCst) as f32 / capacity as f32;
        packet_counter += 1;
        let this_sequence = sequence;
        sequence += 1;

        if faults.disconnect_after_packets > 0 && packet_counter > faults.disconnect_after_packets {
            break 'outer;
        }
        let fault_drop = faults.drop_every_nth_packet > 0
            && packet_counter.is_multiple_of(faults.drop_every_nth_packet);
        if suspended || fault_drop || level > DISCARD_LEVEL {
            continue; // discarded: the client sees a sequence gap
        }

        let packet = encode_packet(
            this_sequence,
            started.elapsed().as_secs_f64(),
            level,
            &blocks,
        );
        pending.fetch_add(1, Ordering::SeqCst);
        if tx.try_send(packet).is_err() {
            // Queue full: server discards.
            pending.fetch_sub(1, Ordering::SeqCst);
        }
    }

    drop(tx);
    let _ = writer.join();
}

/// Streaming-enabled channels with their effective rates (MSR / module
/// divisor) and kinds, resolved from the applied device state.
fn active_channels(state: &SimState, config: &SimConfig) -> Vec<ActiveChannel> {
    let master_rate = enum_numeric(&state.items[0].settings, "Master Sampling Rate")
        .unwrap_or(f64::from(config.system.master_sampling_rate));
    let raw_format = state.items[0]
        .settings
        .as_array()
        .and_then(|settings| {
            settings.iter().find(|s| {
                s.get("Name").and_then(Value::as_str) == Some("Analog Data Streaming Format")
            })
        })
        .and_then(|s| s.get("Value"))
        .and_then(Value::as_i64)
        == Some(1);

    let mut channels = Vec::new();
    for (module_idx, module) in state
        .items
        .iter()
        .enumerate()
        .filter(|(_, i)| i.item_type == "Module")
    {
        let divisor = enum_numeric(&module.settings, "Sample Rate").unwrap_or(256.0);
        let module_rate_hz = master_rate / divisor;
        let slot = module.slot_index.and_then(|i| config.slots.get(i));
        for (channel_offset, child_idx) in descendant_channels(state, module_idx).iter().enumerate()
        {
            let channel = &state.items[*child_idx];
            let streaming = channel
                .data
                .as_array()
                .and_then(|data| {
                    data.iter()
                        .find(|d| d.get("Name").and_then(Value::as_str) == Some("Streaming State"))
                })
                .and_then(|d| d.get("Value"))
                .and_then(Value::as_i64)
                == Some(1);
            if !streaming || channel.current_mode == 0 {
                continue;
            }
            // GPS channels (XMC237): wire format unknown — no capture with a
            // fix exists yet, so the sim does not emit them (assumptions A30).
            if channel.item_name_identifier == GPS_CHANNEL_IDENTIFIER {
                continue;
            }
            let sim_channel =
                slot.and_then(|slot| slot.channels.iter().find(|c| c.index == channel_offset + 1));
            let kind = if TACHO_CHANNEL_IDENTIFIERS.contains(&channel.item_name_identifier) {
                let rpm = match sim_channel.map(|c| c.signal.clone()) {
                    Some(SignalConfig::Rpm { rpm }) => rpm,
                    _ => 3000.0,
                };
                ChannelKind::Tacho { rpm }
            } else if CAN_CHANNEL_IDENTIFIERS.contains(&channel.item_name_identifier) {
                let playback = sim_channel.map(|c| c.playback.clone()).unwrap_or_default();
                let sent = vec![0; playback.len()];
                ChannelKind::Can { playback, sent }
            } else {
                ChannelKind::Analog {
                    signal: sim_channel.map(|c| c.signal.clone()).unwrap_or_default(),
                    raw: raw_format,
                }
            };
            channels.push(ActiveChannel {
                item_id: channel.item_id,
                rate_hz: channel_rate_hz(&channel.settings, master_rate, module_rate_hz),
                kind,
                emitted: 0,
            });
        }
    }
    channels
}

/// All Channel descendants of `idx` in depth-first order — the 1-based
/// positions clients count (nested channels like TAC221 Scope included).
fn descendant_channels(state: &SimState, idx: usize) -> Vec<usize> {
    let mut found = Vec::new();
    let mut stack: Vec<usize> = state.items[idx].children.iter().rev().copied().collect();
    while let Some(child) = stack.pop() {
        if state.items[child].item_type == "Channel" {
            found.push(child);
            stack.extend(state.items[child].children.iter().rev().copied());
        }
    }
    found
}

/// A channel-level "Sample Rate" overrides the module rate. TAC221 Scope
/// rates are MSR multipliers ("MSR Multiplied by 0.5"); divisor-style
/// channel rates would divide.
fn channel_rate_hz(channel_settings: &Value, master_rate: f64, module_rate_hz: f64) -> f64 {
    let Some(setting) = channel_settings.as_array().and_then(|s| {
        s.iter()
            .find(|s| s.get("Name").and_then(Value::as_str) == Some("Sample Rate"))
    }) else {
        return module_rate_hz;
    };
    let value = setting.get("Value").and_then(Value::as_i64);
    let Some(chosen) = setting
        .get("SupportedValues")
        .and_then(Value::as_array)
        .and_then(|vs| {
            vs.iter()
                .find(|v| v.get("Id").and_then(Value::as_i64) == value)
        })
    else {
        return module_rate_hz;
    };
    let Some(numeric) = chosen.get("Numeric").and_then(Value::as_f64) else {
        return module_rate_hz;
    };
    let multiplied = chosen
        .get("Description")
        .and_then(Value::as_str)
        .is_some_and(|d| d.contains("Multiplied"));
    if multiplied {
        master_rate * numeric
    } else {
        master_rate / numeric
    }
}

/// `Numeric` of the currently selected SupportedValue of `name`.
fn enum_numeric(settings: &Value, name: &str) -> Option<f64> {
    let setting = settings
        .as_array()?
        .iter()
        .find(|s| s.get("Name").and_then(Value::as_str) == Some(name))?;
    let value = setting.get("Value").and_then(Value::as_i64)?;
    setting
        .get("SupportedValues")
        .and_then(Value::as_array)?
        .iter()
        .find(|v| v.get("Id").and_then(Value::as_i64) == Some(value))?
        .get("Numeric")
        .and_then(Value::as_f64)
}

// ---- wire encoding (independent of quantus-client; manual byte layout) ----

fn encode_packet(
    sequence: u64,
    transmit_ts: f64,
    buffer_level: f32,
    blocks: &[Vec<u8>],
) -> Vec<u8> {
    let payload_size: usize = blocks.iter().map(Vec::len).sum();
    let mut packet = Vec::with_capacity(32 + payload_size);
    packet.extend_from_slice(&sequence.to_le_bytes());
    packet.extend_from_slice(&transmit_ts.to_le_bytes());
    packet.extend_from_slice(&buffer_level.to_le_bytes());
    packet.extend_from_slice(&(payload_size as u32).to_le_bytes());
    packet.extend_from_slice(&0xFFFEu32.to_le_bytes());
    packet.extend_from_slice(&0u32.to_le_bytes()); // PayloadType: data
    for block in blocks {
        packet.extend_from_slice(block);
    }
    packet
}

fn analog_block_f32(channel_id: i32, timestamp_ns: u64, samples: &[f32]) -> Vec<u8> {
    let data_len = samples.len() * 4;
    let mut block = Vec::with_capacity(24 + 20 + data_len);
    // Generic channel header; ChannelDataSize counts only the sample bytes.
    block.extend_from_slice(&channel_id.to_le_bytes());
    block.extend_from_slice(&0i32.to_le_bytes()); // SampleType: f32
    block.extend_from_slice(&0u32.to_le_bytes()); // ChannelType: analog
    block.extend_from_slice(&(data_len as u32).to_le_bytes());
    block.extend_from_slice(&timestamp_ns.to_le_bytes());
    // Analog specific header.
    let min = samples.iter().copied().fold(f32::INFINITY, f32::min);
    let max = samples.iter().copied().fold(f32::NEG_INFINITY, f32::max);
    block.extend_from_slice(&0i32.to_le_bytes()); // integrity: OK
    block.extend_from_slice(&0i32.to_le_bytes()); // level crossing (unsupported)
    block.extend_from_slice(&max.abs().min(1.0).to_le_bytes()); // Level (0..1 of FS, approximated)
    block.extend_from_slice(&min.to_le_bytes());
    block.extend_from_slice(&max.to_le_bytes());
    for sample in samples {
        block.extend_from_slice(&sample.to_le_bytes());
    }
    block
}

/// Raw mode, 24-bit fixed point: analog specific header, then the f32 scaling
/// factor, then 3 bytes per sample. ChannelDataSize counts only the sample
/// bytes (excludes header and scaling factor).
fn analog_block_raw24(channel_id: i32, timestamp_ns: u64, samples: &[f32]) -> Vec<u8> {
    let scaling: f32 = RAW_FULL_SCALE / 2u32.pow(31) as f32;
    let data_len = samples.len() * 3;
    let mut block = Vec::with_capacity(24 + 20 + 4 + data_len);
    block.extend_from_slice(&channel_id.to_le_bytes());
    block.extend_from_slice(&2i32.to_le_bytes()); // SampleType: 24-bit fixed
    block.extend_from_slice(&0u32.to_le_bytes()); // ChannelType: analog
    block.extend_from_slice(&(data_len as u32).to_le_bytes());
    block.extend_from_slice(&timestamp_ns.to_le_bytes());
    let min = samples.iter().copied().fold(f32::INFINITY, f32::min);
    let max = samples.iter().copied().fold(f32::NEG_INFINITY, f32::max);
    block.extend_from_slice(&0i32.to_le_bytes());
    block.extend_from_slice(&0i32.to_le_bytes());
    block.extend_from_slice(&(max.abs() / RAW_FULL_SCALE).min(1.0).to_le_bytes());
    block.extend_from_slice(&min.to_le_bytes());
    block.extend_from_slice(&max.to_le_bytes());
    block.extend_from_slice(&scaling.to_le_bytes());
    for sample in samples {
        // Decoders assemble raw32 = b2<<24 | b1<<16 | b0<<8 and multiply by
        // the scaling factor, so emit the top three bytes of value/scaling.
        let raw32 = (sample / scaling).max(i32::MIN as f32).min(i32::MAX as f32) as i32;
        let shifted = (raw32 as u32) >> 8;
        block.push((shifted & 0xFF) as u8);
        block.push(((shifted >> 8) & 0xFF) as u8);
        block.push(((shifted >> 16) & 0xFF) as u8);
    }
    block
}

/// Tacho block: f64 event timestamps (ms from epoch start), no specific header.
fn tacho_block(channel_id: i32, events_ms: &[f64]) -> Vec<u8> {
    let data_len = events_ms.len() * 8;
    let mut block = Vec::with_capacity(24 + data_len);
    block.extend_from_slice(&channel_id.to_le_bytes());
    block.extend_from_slice(&0i32.to_le_bytes());
    block.extend_from_slice(&1u32.to_le_bytes()); // ChannelType: tacho
    block.extend_from_slice(&(data_len as u32).to_le_bytes());
    block.extend_from_slice(
        &((events_ms.first().copied().unwrap_or(0.0) * 1e6) as u64).to_le_bytes(),
    );
    for event in events_ms {
        block.extend_from_slice(&event.to_le_bytes());
    }
    block
}

/// One CAN message: f64 timestamp (s), u32 id, header/format/type bytes, DLC
/// (actual byte count on the wire), then payload. The payload here is a
/// counter pattern so tests can verify frame ordering.
fn can_message(timestamp_s: f64, id: u32, dlc: u8, counter: u64) -> Vec<u8> {
    let mut message = Vec::with_capacity(16 + dlc as usize);
    message.extend_from_slice(&timestamp_s.to_le_bytes());
    message.extend_from_slice(&id.to_le_bytes());
    message.push(0); // header
    message.push(0); // frame format
    message.push(0); // frame type
    message.push(dlc);
    for i in 0..dlc {
        message.push((counter.wrapping_add(i as u64) & 0xFF) as u8);
    }
    message
}

/// CAN block: 24-byte reserved header, then messages. ChannelDataSize counts
/// only the message bytes.
fn can_block(channel_id: i32, messages: &[Vec<u8>]) -> Vec<u8> {
    let data_len: usize = messages.iter().map(Vec::len).sum();
    let mut block = Vec::with_capacity(24 + 24 + data_len);
    block.extend_from_slice(&channel_id.to_le_bytes());
    block.extend_from_slice(&0i32.to_le_bytes());
    block.extend_from_slice(&2u32.to_le_bytes()); // ChannelType: CAN
    block.extend_from_slice(&(data_len as u32).to_le_bytes());
    block.extend_from_slice(&0u64.to_le_bytes()); // per-message timestamps carry the time
    block.extend_from_slice(&[0u8; 24]); // reserved header
    for message in messages {
        block.extend_from_slice(message);
    }
    block
}
