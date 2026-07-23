//! Rack-description config: which chassis, which modules in which slots, what
//! signal each channel generates, and fault-injection knobs.

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct SimConfig {
    pub system: SystemConfig,
    #[serde(default)]
    pub server: ServerConfig,
    #[serde(default)]
    pub slots: Vec<SlotConfig>,
    #[serde(default)]
    pub faults: FaultsConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SystemConfig {
    /// Controller model: "PQ20G2", "PQ30G2", "MicroQ", or "PQ45".
    pub chassis: String,
    pub serial: String,
    /// One of the seven supported master rates (Hz), e.g. 131072 or 204800.
    pub master_sampling_rate: u32,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct ServerConfig {
    /// REST port; 0 picks an ephemeral port (useful in tests).
    pub rest_port: u16,
    /// Binary stream port; 0 picks an ephemeral port.
    pub stream_port: u16,
    pub websocket_port: u16,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            rest_port: 8080,
            stream_port: 8085,
            websocket_port: 8090,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct SlotConfig {
    pub slot: u32,
    /// Module model name, e.g. "ICS425", "THM427", "MIC42X7", "WSB42X2".
    pub module: String,
    /// Boot with this operation-mode id instead of the template default
    /// (simulates state persisted by a previous session, e.g. 0 = Disabled).
    #[serde(default)]
    pub boot_mode: Option<i64>,
    /// Attach under the Controller instead of the SC (chassis built-ins like
    /// the MicroQ's XMC237, which precede the SC in /item/list order).
    #[serde(default)]
    pub builtin: bool,
    /// Per-channel signal definitions; channels not listed default to a
    /// 100 Hz unit sine.
    #[serde(default)]
    pub channels: Vec<SimChannelConfig>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SimChannelConfig {
    /// 1-based channel position within the module.
    pub index: usize,
    #[serde(default)]
    pub signal: SignalConfig,
    /// Boot with Streaming State already Enabled (simulates a channel left
    /// streaming by a previous session; settings persist across power cycles).
    #[serde(default)]
    pub boot_streaming: bool,
    /// CAN frame playback (CAN channels only): periodic messages this bus
    /// "receives".
    #[serde(default)]
    pub playback: Vec<CanPlayback>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CanPlayback {
    /// Arbitration id.
    pub id: u32,
    pub period_ms: u64,
    /// Payload length in bytes (the wire DLC field carries a byte count).
    pub dlc: u8,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum SignalConfig {
    Sine {
        frequency_hz: f64,
        amplitude: f64,
        #[serde(default)]
        offset: f64,
    },
    Constant {
        value: f64,
    },
    Ramp {
        from: f64,
        to: f64,
        period_s: f64,
    },
    /// Tacho channels: a shaft turning at constant speed, one pulse per rev.
    Rpm {
        rpm: f64,
    },
}

impl Default for SignalConfig {
    fn default() -> Self {
        SignalConfig::Sine {
            frequency_hz: 100.0,
            amplitude: 1.0,
            offset: 0.0,
        }
    }
}

impl SignalConfig {
    /// Sample value at time `t` seconds from epoch start.
    pub fn value_at(&self, t: f64) -> f32 {
        match self {
            SignalConfig::Sine {
                frequency_hz,
                amplitude,
                offset,
            } => {
                (offset + amplitude * (2.0 * std::f64::consts::PI * frequency_hz * t).sin()) as f32
            }
            SignalConfig::Constant { value } => *value as f32,
            SignalConfig::Ramp { from, to, period_s } => {
                let phase = (t / period_s).fract();
                (from + (to - from) * phase) as f32
            }
            // Not a sampled waveform; tacho generation uses the rpm directly.
            SignalConfig::Rpm { .. } => 0.0,
        }
    }
}

/// Deterministic misbehavior for testing client recovery paths.
#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct FaultsConfig {
    /// Sleep this long before every `/system/settings/apply` completes.
    pub apply_delay_ms: u64,
    /// Answer `/system/settings/apply` with HTTP 204 and no body, as MicroQ
    /// firmware does (2026-07-23), instead of a status document.
    pub apply_no_content: bool,
    /// Drop every Nth stream packet (sequence still advances -> visible gap).
    /// 0 disables.
    pub drop_every_nth_packet: u64,
    /// Close the stream socket after this many packets. 0 disables.
    pub disconnect_after_packets: u64,
    /// Server-side buffer capacity in packets; above 45% of this the sim
    /// discards packets like the real device.
    pub stream_buffer_packets: usize,
}

impl Default for FaultsConfig {
    fn default() -> Self {
        Self {
            apply_delay_ms: 0,
            apply_no_content: false,
            drop_every_nth_packet: 0,
            disconnect_after_packets: 0,
            stream_buffer_packets: 64,
        }
    }
}
