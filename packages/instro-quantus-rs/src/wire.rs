//! Binary stream wire-format layout (QServer Q2.x, "Data Streaming Reference").
//!
//! All multi-byte fields are little-endian, tightly packed, no padding. These
//! constants are transcribed from the manual's byte-layout tables
//! (`docs/api-notes.md` section 5); the hand-built packets in
//! `tests/golden_packets.rs` are the independent check that they match the spec.

/// `ByteOrderMarker` field value in every packet header.
pub const BYTE_ORDER_MARKER: u32 = 0xFFFE;

/// Packet header: u64 SequenceNumber, f64 TransmitTimestamp, f32 BufferLevel,
/// u32 PayloadSize, u32 ByteOrderMarker, u32 PayloadType.
pub const PACKET_HEADER_LEN: usize = 32;

/// Generic channel header: i32 ChannelId, i32 SampleType, u32 ChannelType,
/// u32 ChannelDataSize, u64 Timestamp/Offset (ns).
///
/// ChannelDataSize counts ONLY the sample/message bytes that follow the
/// type-specific header (and, in Raw mode, the f32 scaling factor) — the
/// specific header itself is not included. Confirmed by the vendor's
/// StreamData.py parser.
pub const GENERIC_CHANNEL_HEADER_LEN: usize = 24;

/// Analog specific header, Processed mode: i32 ChannelIntegrity,
/// i32 LevelCrossingOccurred, f32 Level, f32 Min, f32 Max.
pub const ANALOG_HEADER_LEN_PROCESSED: usize = 20;

/// Analog specific header, Raw mode: Processed fields + f32 ScalingFactor.
pub const ANALOG_HEADER_LEN_RAW: usize = 24;

/// CAN channel blocks carry a reserved header before the messages.
pub const CAN_RESERVED_HEADER_LEN: usize = 24;

/// GPS (beta) channel blocks carry a 12-byte header before ASCII NMEA data.
pub const GPS_HEADER_LEN: usize = 12;

/// Fixed-size prefix of one CAN message: f64 timestamp (s), u32 arbitration id,
/// u8 header, u8 frame format, u8 frame type, u8 DLC; followed by 1..=64 data bytes.
pub const CAN_MESSAGE_PREFIX_LEN: usize = 16;

/// Server-side buffer level above which QServer starts discarding data.
pub const SERVER_DISCARD_BUFFER_LEVEL: f32 = 0.45;

/// `PayloadType` values. Anything other than `Data` is skipped by consumers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum PayloadType {
    Data = 0,
}

/// `ChannelType` field of the generic channel header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum ChannelType {
    Analog = 0,
    Tacho = 1,
    Can = 2,
    Gps = 3,
    Triggered = 4,
    TriggeredStatus = 5,
    TriggeredStats = 6,
}

/// `SampleType` for analog channels. `Float32` is Processed mode; the fixed-point
/// types are Raw mode and require client-side scaling by the header's ScalingFactor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum AnalogSampleType {
    Float32 = 0,
    Fixed16 = 1,
    Fixed24 = 2,
    Fixed32 = 3,
}

/// `ChannelIntegrity` field of the analog specific header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum ChannelIntegrity {
    NotApplicable = -1,
    Ok = 0,
    Overload = 1,
    ShortCircuit = 2,
    OpenCircuit = 3,
    AdcError = 4,
}
