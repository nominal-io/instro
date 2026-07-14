//! Minimal DBC parser and CAN signal decoder (PLAN.md D14). Parses the
//! `BO_`/`SG_` subset (Intel/Motorola byte order, signed/unsigned,
//! factor/offset, multiplexing incl. the extended `m<N>M` token); everything
//! else in the file is ignored. Physical value = raw * factor + offset.
//!
//! Frames are matched by 29-bit id (the DBC extended flag is masked off, so a
//! standard and an extended message with the same number collide — last one
//! wins; the wire frame_format semantics are unverified pre-hardware).
//! Signals whose bit span exceeds the received frame's data length are
//! skipped, never zero-filled.

use crate::error::{Error, Result};
use std::collections::HashMap;

/// Masks off the extended-frame flag (bit 31) DBC files set on 29-bit ids.
const CAN_ID_MASK: u32 = 0x1FFF_FFFF;

/// Vector tools park signals not assigned to any real message on this
/// pseudo-message id; it must never be matched against wire frames (masked it
/// would collide with CAN id 0).
const VECTOR_INDEPENDENT_SIG_MSG: u32 = 0xC000_0000;

#[derive(Debug, Clone, PartialEq)]
enum Mux {
    None,
    /// The multiplexor switch signal (`M`).
    Switch,
    /// Decoded only when the switch equals this value (`m<N>`).
    Value(u64),
}

#[derive(Debug, Clone)]
struct SignalSpec {
    name: String,
    start_bit: u32,
    size: u32,
    little_endian: bool,
    signed: bool,
    factor: f64,
    offset: f64,
    mux: Mux,
}

#[derive(Debug, Clone)]
struct MessageSpec {
    signals: Vec<SignalSpec>,
}

/// Decodes CAN frames into named physical signal values using one DBC file.
#[derive(Debug, Clone)]
pub struct CanDecoder {
    /// Keyed by 29-bit id (DBC extended flag masked off).
    messages: HashMap<u32, MessageSpec>,
}

impl CanDecoder {
    pub fn from_path(path: impl AsRef<std::path::Path>) -> Result<Self> {
        let path = path.as_ref();
        let text = std::fs::read_to_string(path)
            .map_err(|e| Error::Config(format!("cannot read DBC {}: {e}", path.display())))?;
        Self::from_dbc_str(&text)
    }

    pub fn from_dbc_str(text: &str) -> Result<Self> {
        let mut messages: HashMap<u32, MessageSpec> = HashMap::new();
        // None = before any BO_; Some(None) = inside an ignored message
        // (Vector pseudo-message); Some(Some(key)) = inside a real message.
        // Non-BO_/SG_ lines (CM_, VAL_, blanks) never end a block: signals
        // only attach via "SG_ " and other sections are simply skipped.
        let mut current: Option<Option<u32>> = None;
        for (line_no, line) in text.lines().enumerate() {
            let trimmed = line.trim();
            if let Some(rest) = trimmed.strip_prefix("BO_ ") {
                let id = parse_message_id(rest).ok_or_else(|| {
                    Error::Config(format!("bad BO_ line {}: {trimmed}", line_no + 1))
                })?;
                if id == VECTOR_INDEPENDENT_SIG_MSG {
                    current = Some(None);
                    continue;
                }
                let key = id & CAN_ID_MASK;
                messages.insert(
                    key,
                    MessageSpec {
                        signals: Vec::new(),
                    },
                );
                current = Some(Some(key));
            } else if let Some(rest) = trimmed.strip_prefix("SG_ ") {
                let Some(block) = current else {
                    return Err(Error::Config(format!(
                        "SG_ before any BO_ at line {}",
                        line_no + 1
                    )));
                };
                let Some(key) = block else {
                    continue; // signal of an ignored pseudo-message
                };
                let signal = parse_signal(rest).ok_or_else(|| {
                    Error::Config(format!("bad SG_ line {}: {trimmed}", line_no + 1))
                })?;
                messages
                    .get_mut(&key)
                    .expect("current message exists")
                    .signals
                    .push(signal);
            }
        }
        if messages.is_empty() {
            return Err(Error::Config(
                "DBC defines no messages (no BO_ lines)".into(),
            ));
        }
        Ok(CanDecoder { messages })
    }

    /// Decode one frame to (signal name, physical value) pairs. `None` means
    /// the id is not in the DBC; the caller counts it as an unknown frame.
    /// A `Some(empty)` means the id is known but no signal fit the received
    /// data (e.g. a truncated frame).
    pub fn decode(&self, id: u32, data: &[u8]) -> Option<Vec<(String, f64)>> {
        let message = self.messages.get(&(id & CAN_ID_MASK))?;
        let switch = message
            .signals
            .iter()
            .find(|s| s.mux == Mux::Switch)
            .and_then(|s| extract_raw(s, data));
        let mut values = Vec::with_capacity(message.signals.len());
        for signal in &message.signals {
            if let Mux::Value(selector) = signal.mux
                && switch != Some(selector)
            {
                continue;
            }
            let Some(raw) = extract_raw(signal, data) else {
                continue;
            };
            let raw_value = if signal.signed {
                sign_extend(raw, signal.size) as f64
            } else {
                raw as f64
            };
            values.push((
                signal.name.clone(),
                raw_value * signal.factor + signal.offset,
            ));
        }
        Some(values)
    }
}

/// `BO_ <id> <Name>: <dlc> <sender>` -> id (extended flag bit still set).
fn parse_message_id(rest: &str) -> Option<u32> {
    rest.split_whitespace().next()?.parse::<u32>().ok()
}

/// `<Name> [M|m<N>] : <start>|<size>@<endian><sign> (<factor>,<offset>) ...`
fn parse_signal(rest: &str) -> Option<SignalSpec> {
    let (head, tail) = rest.split_once(':')?;
    let mut head_tokens = head.split_whitespace();
    let name = head_tokens.next()?.to_string();
    let mux = match head_tokens.next() {
        None => Mux::None,
        Some("M") => Mux::Switch,
        // "m<N>" plain multiplexed; "m<N>M" (extended multiplexing) is both a
        // multiplexed signal and a nested switch — treated as plain m<N> here
        // (nested selections via SG_MUL_VAL_ are not modeled).
        Some(m) => {
            let core = m.strip_suffix('M').unwrap_or(m);
            Mux::Value(core.strip_prefix('m')?.parse().ok()?)
        }
    };

    let mut tail_tokens = tail.split_whitespace();
    let layout = tail_tokens.next()?; // e.g. "24|16@1+"
    let (start, rest_layout) = layout.split_once('|')?;
    let (size, order_sign) = rest_layout.split_once('@')?;
    let start_bit: u32 = start.parse().ok()?;
    let size: u32 = size.parse().ok()?;
    if size == 0 || size > 64 {
        return None;
    }
    let mut order_chars = order_sign.chars();
    let little_endian = match order_chars.next()? {
        '1' => true,
        '0' => false,
        _ => return None,
    };
    let signed = match order_chars.next()? {
        '-' => true,
        '+' => false,
        _ => return None,
    };

    let scale = tail_tokens.next()?; // e.g. "(0.125,0)"
    let scale = scale.strip_prefix('(')?.strip_suffix(')')?;
    let (factor, offset) = scale.split_once(',')?;
    Some(SignalSpec {
        name,
        start_bit,
        size,
        little_endian,
        signed,
        factor: factor.parse().ok()?,
        offset: offset.parse().ok()?,
        mux,
    })
}

/// Extract the raw (unscaled, unsigned) bits. Works on any frame length
/// (classic CAN or CAN FD up to 64 bytes). None when the signal's bit span
/// extends beyond the received data — a short/truncated frame must skip the
/// signal, not fabricate zeros.
fn extract_raw(signal: &SignalSpec, data: &[u8]) -> Option<u64> {
    let size = signal.size as usize;
    let start = signal.start_bit as usize;
    if signal.little_endian {
        // Intel: start_bit is the LSB position; bit i lives at data[i/8] bit (i%8).
        if start + size > data.len() * 8 {
            return None;
        }
        let mut raw = 0u64;
        for k in 0..size {
            let bit = start + k;
            let b = (data[bit / 8] >> (bit % 8)) & 1;
            raw |= u64::from(b) << k;
        }
        Some(raw)
    } else {
        // Motorola: start_bit is the MSB position in per-byte 7..0 numbering;
        // the signal walks toward bit 0, then into the next byte's bit 7.
        let mut byte = start / 8;
        let mut bit_in_byte = start % 8;
        let mut raw = 0u64;
        for _ in 0..size {
            if byte >= data.len() {
                return None;
            }
            let b = (data[byte] >> bit_in_byte) & 1;
            raw = (raw << 1) | u64::from(b);
            if bit_in_byte == 0 {
                byte += 1;
                bit_in_byte = 7;
            } else {
                bit_in_byte -= 1;
            }
        }
        Some(raw)
    }
}

fn sign_extend(raw: u64, size: u32) -> i64 {
    if size == 64 || raw & (1u64 << (size - 1)) == 0 {
        raw as i64
    } else {
        (raw | !((1u64 << size) - 1)) as i64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DBC: &str = r#"VERSION ""

BU_: ECU

BO_ 2364539904 EEC1: 8 ECU
 SG_ EngineSpeed : 24|16@1+ (0.125,0) [0|8031.875] "rpm" Vector__XXX

BO_ 2566869221 VehicleData: 8 ECU
 SG_ Counter : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ CoolantTemp : 8|8@1+ (1,-40) [-40|215] "degC" Vector__XXX

BO_ 256 Motorola: 8 ECU
 SG_ BigEndian16 : 7|16@0+ (0.1,0) [0|6553.5] "" Vector__XXX
 SG_ SignedByte : 32|8@1- (1,0) [-128|127] "" Vector__XXX

BO_ 512 Muxed: 8 ECU
 SG_ Selector M : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ OnlyWhen2 m2 : 8|16@1+ (1,0) [0|65535] "" Vector__XXX
"#;

    #[test]
    fn decodes_intel_signals_with_factor_and_offset() {
        let decoder = CanDecoder::from_dbc_str(DBC).unwrap();
        // EngineSpeed: bits 24..40 little endian -> bytes 3..5, raw 0x1000 = 4096 -> 512 rpm.
        let data = [0, 0, 0, 0x00, 0x10, 0, 0, 0];
        let values = decoder.decode(0x0CF00400, &data).unwrap();
        assert_eq!(values, vec![("EngineSpeed".to_string(), 512.0)]);

        let data = [7, 65, 0, 0, 0, 0, 0, 0]; // Counter=7, CoolantTemp raw 65 -> 25 degC
        let values = decoder.decode(0x18FF50E5, &data).unwrap();
        assert_eq!(values[0], ("Counter".to_string(), 7.0));
        assert_eq!(values[1], ("CoolantTemp".to_string(), 25.0));
    }

    #[test]
    fn decodes_motorola_and_signed_signals() {
        let decoder = CanDecoder::from_dbc_str(DBC).unwrap();
        // BigEndian16 at start bit 7: bytes 0..2 big endian. 0x0102 = 258 -> 25.8.
        let data = [0x01, 0x02, 0, 0, 0xFE, 0, 0, 0];
        let values = decoder.decode(256, &data).unwrap();
        assert_eq!(values[0].0, "BigEndian16");
        assert!((values[0].1 - 25.8).abs() < 1e-9);
        assert_eq!(values[1], ("SignedByte".to_string(), -2.0)); // 0xFE signed
    }

    #[test]
    fn multiplexed_signals_follow_the_switch() {
        let decoder = CanDecoder::from_dbc_str(DBC).unwrap();
        let selected = decoder
            .decode(512, &[2, 0x34, 0x12, 0, 0, 0, 0, 0])
            .unwrap();
        assert!(selected.contains(&("OnlyWhen2".to_string(), 0x1234 as f64)));
        let unselected = decoder
            .decode(512, &[1, 0x34, 0x12, 0, 0, 0, 0, 0])
            .unwrap();
        assert_eq!(unselected, vec![("Selector".to_string(), 1.0)]);
    }

    #[test]
    fn unknown_ids_and_short_frames_are_handled() {
        let decoder = CanDecoder::from_dbc_str(DBC).unwrap();
        assert!(decoder.decode(0x7FF, &[0; 8]).is_none());
        // Truncated frame: EngineSpeed spans bytes 3..5, so a 2-byte frame
        // skips it rather than fabricating a zero reading.
        let values = decoder.decode(0x0CF00400, &[0, 0]).unwrap();
        assert!(values.is_empty());
        // VehicleData's byte-0/1 signals still decode from a 2-byte frame.
        let values = decoder.decode(0x18FF50E5, &[9, 50]).unwrap();
        assert_eq!(values[0], ("Counter".to_string(), 9.0));
        assert_eq!(values[1], ("CoolantTemp".to_string(), 10.0));
    }

    #[test]
    fn can_fd_frames_decode_beyond_eight_bytes() {
        let dbc = r#"BO_ 768 FdFrame: 64 ECU
 SG_ LateSignal : 96|16@1+ (0.01,0) [0|655.35] "" Vector__XXX
"#;
        let decoder = CanDecoder::from_dbc_str(dbc).unwrap();
        let mut data = vec![0u8; 64];
        data[12] = 0x10; // bits 96.. -> bytes 12..14 little endian
        data[13] = 0x27; // 0x2710 = 10000 -> 100.0
        let values = decoder.decode(768, &data).unwrap();
        assert_eq!(values, vec![("LateSignal".to_string(), 100.0)]);
    }

    #[test]
    fn vector_pseudo_message_is_ignored_not_id_zero() {
        let dbc = r#"BO_ 3221225472 VECTOR__INDEPENDENT_SIG_MSG: 8 Vector__XXX
 SG_ Orphaned : 0|8@1+ (1,0) [0|255] "" Vector__XXX

BO_ 0 RealIdZero: 8 ECU
 SG_ Actual : 0|8@1+ (1,0) [0|255] "" Vector__XXX
"#;
        let decoder = CanDecoder::from_dbc_str(dbc).unwrap();
        let values = decoder.decode(0, &[42, 0, 0, 0, 0, 0, 0, 0]).unwrap();
        assert_eq!(values, vec![("Actual".to_string(), 42.0)]);
    }

    #[test]
    fn extended_multiplexing_token_and_interleaved_sections_parse() {
        // m1M (extended mux switch+value) and a CM_ line between signals must
        // not reject the file; CRLF line endings and tabs are tolerated.
        let dbc = "BO_ 256 Mixed: 8 ECU\r\n SG_ Sel M : 0|8@1+ (1,0) [0|3] \"\" X\r\nCM_ SG_ 256 Sel \"selector\";\r\n\tSG_ Nested m1M : 8|8@1+ (1,0) [0|255] \"\" X\r\n"
            .to_string();
        let decoder = CanDecoder::from_dbc_str(&dbc).unwrap();
        let values = decoder.decode(256, &[1, 7, 0, 0, 0, 0, 0, 0]).unwrap();
        assert!(values.contains(&("Nested".to_string(), 7.0)));
        let values = decoder.decode(256, &[0, 7, 0, 0, 0, 0, 0, 0]).unwrap();
        assert_eq!(values, vec![("Sel".to_string(), 0.0)]);
    }

    #[test]
    fn rejects_malformed_files() {
        assert!(CanDecoder::from_dbc_str("").is_err());
        assert!(CanDecoder::from_dbc_str("SG_ Orphan : 0|8@1+ (1,0)").is_err());
        assert!(CanDecoder::from_dbc_str("BO_ notanid Name: 8 ECU").is_err());
    }
}
