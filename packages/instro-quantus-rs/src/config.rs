//! Rack configuration: the declarative description of how the device should be
//! set up. Enum values are given as human-readable descriptions (e.g. mode
//! "ICP® Input", setting "1 V") and resolved to integer Ids at reconcile time
//! from the device's SupportedValues (PLAN.md D4).

use crate::error::{Error, Result};
use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Debug, Clone, Deserialize)]
pub struct RackConfig {
    /// Optional protocol discriminator, mirroring instro's Modbus/EtherNet-IP
    /// configs; when present it must be "quantus".
    #[serde(default)]
    pub protocol: Option<String>,
    /// Optional in the file so one rack description serves many benches;
    /// connecting without one is an error.
    #[serde(default)]
    pub connection: Option<ConnectionConfig>,
    #[serde(default)]
    pub device: DeviceConfig,
    #[serde(default)]
    pub system: SystemConfig,
    #[serde(default)]
    pub modules: Vec<ModuleConfig>,
}

/// Identity of this rack for consumers that name things after it (e.g.
/// instro's channel-name prefix), mirroring the `device.name` section of
/// instro's Modbus/EtherNet-IP configs.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct DeviceConfig {
    pub name: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ConnectionConfig {
    /// Device IP or mDNS name.
    pub host: String,
    #[serde(default = "default_rest_port")]
    pub rest_port: u16,
}

fn default_rest_port() -> u16 {
    8080
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct SystemConfig {
    /// Master Sampling Rate in Hz (must be one the controller supports).
    pub master_sampling_rate: Option<u32>,
    /// "Processed" (f32 volts) or "Raw" (fixed point). Leave unset to keep the
    /// device's current format.
    pub streaming_format: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ModuleConfig {
    /// Module model name as reported in the item list, e.g. "ICS425".
    pub name: String,
    /// Which occurrence of `name` this config addresses (0-based) when the rack
    /// holds several identical modules.
    #[serde(default)]
    pub occurrence: usize,
    /// Requested per-module sample rate in Hz; snapped to the nearest
    /// achievable MSR divisor and reported back in the reconcile report.
    #[serde(default)]
    pub sample_rate_hz: Option<f64>,
    /// Extra module-level settings by name -> value.
    #[serde(default)]
    pub settings: BTreeMap<String, SettingValue>,
    #[serde(default)]
    pub channels: Vec<ChannelConfig>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ChannelConfig {
    /// 1-based channel position within the module.
    pub index: usize,
    /// Friendly name used in reports and (Phase 3+) stream channel mapping.
    #[serde(default)]
    pub alias: Option<String>,
    /// Operation mode description, e.g. "ICP® Input" or
    /// "Thermocouple Type K Input".
    #[serde(default)]
    pub mode: Option<String>,
    /// Channel settings by name -> value, e.g. { "Voltage Range" = "1 V" }.
    #[serde(default)]
    pub settings: BTreeMap<String, SettingValue>,
    /// Enable this channel's "Streaming State" in its Data array.
    #[serde(default)]
    pub streaming: bool,
}

/// A setting value: an enumeration description ("1 V"), or a raw number for
/// Float/Integer settings.
#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum SettingValue {
    Number(f64),
    Text(String),
}

impl ChannelConfig {
    pub fn display_name(&self) -> String {
        self.alias
            .clone()
            .unwrap_or_else(|| format!("channel {}", self.index))
    }
}

impl RackConfig {
    /// Canonical config format (PLAN.md D13): JSON, matching the instro
    /// `EtherNetIPDevice` precedent and what the Python layer passes through.
    pub fn from_json_str(json: &str) -> Result<Self> {
        let config: Self = serde_json::from_str(json)
            .map_err(|e| Error::Config(format!("invalid JSON config: {e}")))?;
        config.check_protocol()
    }

    /// TOML kept for hand-edited rack files and parity with the simulator.
    pub fn from_toml_str(toml_str: &str) -> Result<Self> {
        let config: Self = toml::from_str(toml_str)
            .map_err(|e| Error::Config(format!("invalid TOML config: {e}")))?;
        config.check_protocol()
    }

    fn check_protocol(self) -> Result<Self> {
        match self.protocol.as_deref() {
            None | Some("quantus") => Ok(self),
            Some(other) => Err(Error::Config(format!(
                "config declares protocol '{other}'; this is a quantus rack config"
            ))),
        }
    }

    /// Load from a file, dispatching on extension: `.json` or `.toml`.
    pub fn from_path(path: impl AsRef<std::path::Path>) -> Result<Self> {
        let path = path.as_ref();
        let raw = std::fs::read_to_string(path)
            .map_err(|e| Error::Config(format!("cannot read {}: {e}", path.display())))?;
        match path
            .extension()
            .and_then(|e| e.to_str())
            .map(str::to_ascii_lowercase)
            .as_deref()
        {
            Some("json") => Self::from_json_str(&raw),
            Some("toml") => Self::from_toml_str(&raw),
            other => Err(Error::Config(format!(
                "unsupported config extension {other:?} for {}; use .json or .toml",
                path.display()
            ))),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const JSON: &str = r#"{
        "connection": { "host": "10.0.0.202" },
        "device": { "name": "test_rack" },
        "system": { "master_sampling_rate": 131072, "streaming_format": "Processed" },
        "modules": [{
            "name": "MIC42X7",
            "sample_rate_hz": 65536.0,
            "channels": [{
                "index": 1,
                "alias": "mic",
                "mode": "Microphone Input",
                "streaming": true,
                "settings": { "Voltage Range": "1.2 V" }
            }]
        }]
    }"#;

    #[test]
    fn json_and_toml_parse_to_the_same_config() {
        let from_json = RackConfig::from_json_str(JSON).unwrap();
        let from_toml = RackConfig::from_toml_str(
            r#"
            [connection]
            host = "10.0.0.202"

            [system]
            master_sampling_rate = 131072
            streaming_format = "Processed"

            [[modules]]
            name = "MIC42X7"
            sample_rate_hz = 65536.0

            [[modules.channels]]
            index = 1
            alias = "mic"
            mode = "Microphone Input"
            streaming = true
            settings = { "Voltage Range" = "1.2 V" }
            "#,
        )
        .unwrap();
        let json_conn = from_json.connection.as_ref().expect("connection in JSON");
        let toml_conn = from_toml.connection.as_ref().expect("connection in TOML");
        assert_eq!(json_conn.host, toml_conn.host);
        assert_eq!(json_conn.rest_port, 8080); // default applied
        assert_eq!(from_json.device.name.as_deref(), Some("test_rack"));
        assert_eq!(from_toml.device.name, None); // optional section
        assert_eq!(from_json.modules[0].name, from_toml.modules[0].name);
        assert_eq!(
            from_json.modules[0].channels[0].alias,
            from_toml.modules[0].channels[0].alias
        );
    }

    #[test]
    fn protocol_discriminator_is_enforced_when_present() {
        let ok = r#"{ "protocol": "quantus", "connection": { "host": "x" } }"#;
        assert!(RackConfig::from_json_str(ok).is_ok());
        let wrong = r#"{ "protocol": "modbus", "connection": { "host": "x" } }"#;
        assert!(matches!(
            RackConfig::from_json_str(wrong),
            Err(Error::Config(_))
        ));
    }

    #[test]
    fn checked_in_example_config_stays_valid() {
        let example = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/fixtures/rack/microq_example.json"
        );
        let config = RackConfig::from_path(example).unwrap();
        assert_eq!(config.modules.len(), 4);
    }

    #[test]
    fn from_path_dispatches_on_extension() {
        let dir = std::env::temp_dir().join("quantus-config-test");
        std::fs::create_dir_all(&dir).unwrap();
        let json_path = dir.join("rack.json");
        std::fs::write(&json_path, JSON).unwrap();
        let config = RackConfig::from_path(&json_path).unwrap();
        assert_eq!(config.modules.len(), 1);

        let bad_path = dir.join("rack.yaml");
        std::fs::write(&bad_path, "{}").unwrap();
        assert!(matches!(
            RackConfig::from_path(&bad_path),
            Err(Error::Config(_))
        ));
    }
}
