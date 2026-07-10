//! Minimal DBC parser and CAN signal decoder (PLAN.md D14). Parses the
//! `BO_`/`SG_` subset (Intel/Motorola byte order, signed/unsigned,
//! factor/offset, simple multiplexing); everything else in the file is
//! ignored. Physical value = raw * factor + offset.

use crate::error::{Error, Result};
use std::collections::HashMap;

/// Masks off the extended-frame flag (bit 31) DBC files set on 29-bit ids.
const CAN_ID_MASK: u32 = 0x1FFF_FFFF;

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
        let mut current: Option<u32> = None;
        for (line_no, line) in text.lines().enumerate() {
            let trimmed = line.trim();
            if let Some(rest) = trimmed.strip_prefix("BO_ ") {
                let id = parse_message_id(rest).ok_or_else(|| {
                    Error::Config(format!("bad BO_ line {}: {trimmed}", line_no + 1))
                })?;
                let key = id & CAN_ID_MASK;
                messages.insert(
                    key,
                    MessageSpec {
                        signals: Vec::new(),
                    },
                );
                current = Some(key);
            } else if let Some(rest) = trimmed.strip_prefix("SG_ ") {
                let Some(key) = current else {
                    return Err(Error::Config(format!(
                        "SG_ before any BO_ at line {}",
                        line_no + 1
                    )));
                };
                let signal = parse_signal(rest).ok_or_else(|| {
                    Error::Config(format!("bad SG_ line {}: {trimmed}", line_no + 1))
                })?;
                messages
                    .get_mut(&key)
                    .expect("current message exists")
                    .signals
                    .push(signal);
            } else if !trimmed.is_empty() {
                // Any other section (CM_, VAL_, ...) ends the message block.
                current = None;
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
        Some(m) => Mux::Value(m.strip_prefix('m')?.parse().ok()?),
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

/// Extract the raw (unscaled, unsigned) bits; None when the layout doesn't
/// fit the frame's data.
fn extract_raw(signal: &SignalSpec, data: &[u8]) -> Option<u64> {
    if data.len() > 8 {
        return None;
    }
    let mut padded = [0u8; 8];
    for (slot, byte) in padded.iter_mut().zip(data) {
        *slot = *byte;
    }
    let mask = if signal.size == 64 {
        u64::MAX
    } else {
        (1u64 << signal.size) - 1
    };
    if signal.little_endian {
        let value = u64::from_le_bytes(padded);
        if signal.start_bit + signal.size > 64 {
            return None;
        }
        Some((value >> signal.start_bit) & mask)
    } else {
        // Motorola: start_bit is the MSB position in per-byte 7..0 numbering.
        let value = u64::from_be_bytes(padded);
        let msb_from_top = (signal.start_bit / 8) * 8 + (7 - signal.start_bit % 8);
        let shift = 64u32.checked_sub(msb_from_top + signal.size)?;
        Some((value >> shift) & mask)
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
        // Short frame: signals fully inside the padded region still decode (as zeros).
        let values = decoder.decode(0x0CF00400, &[0, 0]).unwrap();
        assert_eq!(values, vec![("EngineSpeed".to_string(), 0.0)]);
    }

    #[test]
    fn rejects_malformed_files() {
        assert!(CanDecoder::from_dbc_str("").is_err());
        assert!(CanDecoder::from_dbc_str("SG_ Orphan : 0|8@1+ (1,0)").is_err());
        assert!(CanDecoder::from_dbc_str("BO_ notanid Name: 8 ECU").is_err());
    }
}
