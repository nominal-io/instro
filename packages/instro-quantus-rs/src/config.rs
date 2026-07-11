//! Rack configuration: the declarative description of how the device should be
//! set up. Enum values are given as human-readable descriptions (e.g. mode
//! "ICP® Input", setting "1 V") and resolved to integer Ids at reconcile time
//! from the device's SupportedValues (PLAN.md D4).

use crate::error::{Error, Result};
use serde::Deserialize;
use std::collections::BTreeMap;

/// Top-level sections mirror instro's Modbus/EtherNet-IP configs:
/// `version` / `protocol` / `device` / `connection`, then the
/// protocol-specific payload (`system` + `modules`).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RackConfig {
    #[serde(default = "default_version")]
    pub version: u32,
    /// Protocol discriminator; must be "quantus".
    #[serde(default = "default_protocol")]
    pub protocol: String,
    pub device: DeviceConfig,
    /// Optional in the file so one rack description serves many benches;
    /// connecting without one is an error.
    #[serde(default)]
    pub connection: Option<ConnectionConfig>,
    #[serde(default)]
    pub system: SystemConfig,
    #[serde(default)]
    pub modules: Vec<ModuleConfig>,
}

fn default_version() -> u32 {
    1
}

fn default_protocol() -> String {
    "quantus".into()
}

/// Device metadata, mirroring instro's `DeviceInfo`: `name` is the
/// channel-name prefix on publish (e.g. `my_rig.mic_inlet`).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceConfig {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub manufacturer: String,
    #[serde(default)]
    pub model: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConnectionConfig {
    /// Device IP or mDNS name.
    pub host: String,
    /// QServer REST port; the stream port is discovered via /dataStream/setup.
    #[serde(default = "default_port")]
    pub port: u16,
}

fn default_port() -> u16 {
    8080
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SystemConfig {
    /// Master Sampling Rate in Hz (must be one the controller supports).
    pub master_sampling_rate: Option<u32>,
    /// "Processed" (f32 volts) or "Raw" (fixed point). Leave unset to keep the
    /// device's current format.
    pub streaming_format: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModuleConfig {
    /// Module model name as reported in the item list, e.g. "ICS425".
    pub name: String,
    /// Which occurrence of `name` this config addresses (0-based) when the rack
    /// holds several identical modules.
    #[serde(default)]
    pub occurrence: usize,
    /// Module operation mode description (e.g. "Enabled"). When unset, the
    /// module is driven to "Enabled" iff this config declares anything for it
    /// (a previously Disabled module would otherwise be unconfigurable).
    #[serde(default)]
    pub mode: Option<String>,
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
#[serde(deny_unknown_fields)]
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
    /// DBC file for CAN channels: frames are decoded to per-signal values.
    /// Relative paths resolve against the config file's directory.
    #[serde(default)]
    pub dbc: Option<String>,
    /// Tacho channels: streamed trigger events per shaft revolution, as
    /// delivered on the wire (default 1.0). Consumers divide by this when
    /// converting edge intervals to RPM. Prefer the device's "Trigger On nth
    /// Edge" setting for multi-tooth wheels; if both are used,
    /// pulses_per_rev = teeth / nth.
    #[serde(default)]
    pub pulses_per_rev: Option<f64>,
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
        if self.protocol == "quantus" {
            Ok(self)
        } else {
            Err(Error::Config(format!(
                "Config has protocol '{}', expected 'quantus'.",
                self.protocol
            )))
        }
    }

    /// Load from a file, dispatching on extension: `.json` or `.toml`.
    /// Relative `dbc` paths are resolved against the file's directory.
    pub fn from_path(path: impl AsRef<std::path::Path>) -> Result<Self> {
        let path = path.as_ref();
        let raw = std::fs::read_to_string(path)
            .map_err(|e| Error::Config(format!("cannot read {}: {e}", path.display())))?;
        let config = match path
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
        }?;
        Ok(config.resolve_dbc_paths(path.parent().unwrap_or(std::path::Path::new("."))))
    }

    fn resolve_dbc_paths(mut self, base: &std::path::Path) -> Self {
        for module in &mut self.modules {
            for channel in &mut module.channels {
                if let Some(dbc) = &channel.dbc
                    && std::path::Path::new(dbc).is_relative()
                {
                    channel.dbc = Some(base.join(dbc).to_string_lossy().into_owned());
                }
            }
        }
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const JSON: &str = r#"{
        "version": 1,
        "protocol": "quantus",
        "device": { "name": "test_rack" },
        "connection": { "host": "10.0.0.202" },
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
            [device]
            name = "test_rack"

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
        assert_eq!(json_conn.port, 8080); // default applied
        assert_eq!(from_json.version, 1);
        assert_eq!(from_toml.version, 1); // default applied
        assert_eq!(from_toml.protocol, "quantus"); // default applied
        assert_eq!(from_json.device.name, "test_rack");
        assert_eq!(from_toml.device.name, "test_rack");
        assert_eq!(from_json.device.description, ""); // optional metadata
        assert_eq!(from_json.modules[0].name, from_toml.modules[0].name);
        assert_eq!(
            from_json.modules[0].channels[0].alias,
            from_toml.modules[0].channels[0].alias
        );
    }

    #[test]
    fn unknown_fields_are_rejected_not_ignored() {
        // A typo'd field silently ignored would mean a silently unconfigured
        // rack; the schema forbids extras like the EtherNet/IP config does.
        let top = r#"{ "device": { "name": "x" }, "moduels": [] }"#;
        assert!(matches!(
            RackConfig::from_json_str(top),
            Err(Error::Config(_))
        ));
        let channel = r#"{ "device": { "name": "x" },
            "modules": [{ "name": "ICS425",
                          "channels": [{ "index": 1, "streming": true }] }] }"#;
        assert!(matches!(
            RackConfig::from_json_str(channel),
            Err(Error::Config(_))
        ));
    }

    #[test]
    fn protocol_discriminator_is_enforced() {
        let ok = r#"{ "protocol": "quantus", "device": { "name": "x" } }"#;
        assert!(RackConfig::from_json_str(ok).is_ok());
        let wrong = r#"{ "protocol": "modbus", "device": { "name": "x" } }"#;
        assert!(matches!(
            RackConfig::from_json_str(wrong),
            Err(Error::Config(_))
        ));
        let missing_device = r#"{ "protocol": "quantus" }"#;
        assert!(matches!(
            RackConfig::from_json_str(missing_device),
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
    fn relative_dbc_paths_resolve_against_the_config_dir() {
        let dir = std::env::temp_dir().join("quantus-dbc-path-test");
        std::fs::create_dir_all(&dir).unwrap();
        let config_path = dir.join("rack.json");
        std::fs::write(
            &config_path,
            r#"{
                "device": { "name": "rig" },
                "modules": [{
                    "name": "CAN42S2",
                    "channels": [{ "index": 1, "alias": "bus", "dbc": "vehicle.dbc" }]
                }]
            }"#,
        )
        .unwrap();
        let config = RackConfig::from_path(&config_path).unwrap();
        let resolved = config.modules[0].channels[0].dbc.as_deref().unwrap();
        assert_eq!(resolved, dir.join("vehicle.dbc").to_string_lossy());
        // Inline JSON gets no directory context: the path passes through.
        let inline = RackConfig::from_json_str(
            r#"{ "device": { "name": "rig" },
                 "modules": [{ "name": "CAN42S2",
                               "channels": [{ "index": 1, "dbc": "vehicle.dbc" }] }] }"#,
        )
        .unwrap();
        assert_eq!(
            inline.modules[0].channels[0].dbc.as_deref(),
            Some("vehicle.dbc")
        );
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
