//! The declarative reconcile engine (PLAN.md D8): read the device's item tree,
//! write every declared setting, apply once, and report what was achieved —
//! including snapped sample rates and whether the streaming epoch restarts.

use crate::config::{ModuleConfig, RackConfig, SettingValue};
use crate::error::{Error, Result};
use crate::rest::RestClient;
use crate::settings::{find_setting, resolve_enum_id, set_value, snap_sample_rate};
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Debug, Clone)]
pub struct ReconcileReport {
    pub version: String,
    /// True when apply answered StatusCode 4: the streaming epoch restarts.
    pub restart_required: bool,
    /// Message from a StatusCode 14 ("action has side effects") apply response.
    pub side_effects: Option<String>,
    pub master_sampling_rate_hz: Option<f64>,
    pub modules: Vec<ModuleReport>,
    pub channels: Vec<ChannelReport>,
}

#[derive(Debug, Clone)]
pub struct ModuleReport {
    pub name: String,
    pub item_id: i64,
    pub requested_hz: Option<f64>,
    pub achieved_hz: Option<f64>,
    pub divisor: Option<f64>,
}

#[derive(Debug, Clone)]
pub struct ChannelReport {
    pub alias: String,
    pub item_id: i64,
    pub mode: Option<String>,
    pub streaming: bool,
    /// The module's effective analog rate (Hz); None for modules without a
    /// Sample Rate setting (CAN, tacho-only, outputs).
    pub sample_rate_hz: Option<f64>,
    /// DBC file declared for this (CAN) channel, path already resolved.
    pub dbc: Option<String>,
    /// Tacho channels: streamed trigger events per shaft revolution.
    pub pulses_per_rev: f64,
}

/// A module discovered from `/item/list`: channels are the entries that follow
/// it in list order (controller first, then SC, then module/channel runs).
#[derive(Debug, Clone)]
pub struct DeviceModule {
    pub item_id: i64,
    pub name: String,
    pub channel_ids: Vec<i64>,
}

#[derive(Debug, Clone)]
pub struct DeviceTree {
    pub controller_id: i64,
    pub modules: Vec<DeviceModule>,
}

pub struct Engine {
    rest: RestClient,
}

impl Engine {
    pub fn new(rest: RestClient) -> Self {
        Engine { rest }
    }

    pub fn rest(&self) -> &RestClient {
        &self.rest
    }

    /// Ping the device and assert a Q2.x QServer (PLAN.md D11).
    pub async fn check_connection(&self) -> Result<String> {
        self.rest.ping().await?;
        let version = self.rest.version().await?;
        match parse_major(&version) {
            Some(2) => Ok(version),
            _ => Err(Error::VersionMismatch(format!(
                "expected QServer Q2.x, device reports '{version}'"
            ))),
        }
    }

    pub async fn discover(&self) -> Result<DeviceTree> {
        let list = self.rest.item_list().await?;
        let items = list
            .as_array()
            .ok_or_else(|| Error::Stream(format!("unexpected /item/list body: {list}")))?;
        parse_device_tree(items)
    }

    pub async fn reconcile(&self, config: &RackConfig) -> Result<ReconcileReport> {
        let version = self.check_connection().await?;
        let tree = self.discover().await?;

        let master_rate_hz = self.reconcile_system(config, tree.controller_id).await?;

        let mut module_reports = Vec::new();
        let mut channel_reports = Vec::new();
        for module_config in &config.modules {
            let module = find_module(&tree, module_config)?;
            let (module_report, pair_handled) = self
                .reconcile_module(module_config, &module, master_rate_hz)
                .await?;
            let module_rate = module_report.achieved_hz;
            module_reports.push(module_report);
            let mut reports = self
                .reconcile_channels(module_config, &module, pair_handled, module_rate)
                .await?;
            channel_reports.append(&mut reports);
        }

        let status = self.rest.apply().await?;
        let (restart_required, side_effects) = match status.status_code {
            1 | 3 => (false, None),
            4 => (true, None),
            14 => (true, Some(status.message.clone())),
            _ => {
                return Err(Error::Api {
                    http_status: 200,
                    status,
                });
            }
        };

        Ok(ReconcileReport {
            version,
            restart_required,
            side_effects,
            master_sampling_rate_hz: master_rate_hz,
            modules: module_reports,
            channels: channel_reports,
        })
    }

    /// Set Master Sampling Rate / streaming format when declared, and return
    /// the effective master rate for divisor math.
    async fn reconcile_system(
        &self,
        config: &RackConfig,
        controller_id: i64,
    ) -> Result<Option<f64>> {
        let mut doc = self.rest.item_settings(controller_id).await?;
        let mut dirty = false;

        if let Some(rate) = config.system.master_sampling_rate {
            set_value(
                &mut doc["Settings"],
                "Master Sampling Rate",
                &SettingValue::Text(format!("{rate} Hz")),
            )?;
            dirty = true;
        }
        if let Some(format) = &config.system.streaming_format {
            set_value(
                &mut doc["Settings"],
                "Analog Data Streaming Format",
                &SettingValue::Text(format.clone()),
            )?;
            dirty = true;
        }
        if dirty {
            self.rest.put_item_settings(controller_id, &doc).await?;
        }

        Ok(find_setting(&doc["Settings"], "Master Sampling Rate").and_then(current_enum_numeric))
    }

    /// Drive the module's operation mode to `target` (GET-compare-PUT: no
    /// write when it already matches, preserving open() idempotency).
    async fn reconcile_module_mode(&self, module: &DeviceModule, target: &str) -> Result<()> {
        let mut op_mode = self.rest.item_operation_mode(module.item_id).await?;
        let setting = op_mode["Settings"]
            .as_array()
            .and_then(|s| s.first())
            .cloned()
            .ok_or_else(|| {
                Error::Stream("module operation mode document has no Settings".into())
            })?;
        let target_id = resolve_enum_id(&setting, target)?;
        let current_id = setting.get("Value").and_then(Value::as_i64);
        if current_id != Some(target_id) {
            op_mode["Settings"][0]["Value"] = target_id.into();
            self.rest
                .put_item_operation_mode(module.item_id, &op_mode)
                .await?;
        }
        Ok(())
    }

    /// Returns the module report plus whether channel modes were written as
    /// module-level pair settings (in which case channel op-mode PUTs are skipped).
    async fn reconcile_module(
        &self,
        module_config: &ModuleConfig,
        module: &DeviceModule,
        master_rate_hz: Option<f64>,
    ) -> Result<(ModuleReport, bool)> {
        // High-rate module modes kill channels 2 and 5 (ICS425); the wire
        // schema for that topology is unverified, so reject the combination
        // rather than configure ghosts.
        if let Some(mode) = &module_config.mode
            && mode.contains("High Sample Rate")
            && module_config
                .channels
                .iter()
                .any(|c| c.index == 2 || c.index == 5)
        {
            return Err(Error::Config(format!(
                "module '{}': mode '{mode}' disables channels 2 and 5; remove them from \
                 the config (high-rate topologies are unverified pending hardware)",
                module.name
            )));
        }
        // A Disabled module gates every other setting; drive the mode first
        // (declared mode, else Enabled iff this config declares content).
        let target_mode = module_config.mode.clone().or_else(|| {
            let declares_content = module_config.sample_rate_hz.is_some()
                || !module_config.settings.is_empty()
                || !module_config.channels.is_empty();
            declares_content.then(|| "Enabled".to_string())
        });
        if let Some(target) = &target_mode {
            self.reconcile_module_mode(module, target).await?;
        }

        let mut doc = self.rest.item_settings(module.item_id).await?;
        let mut dirty = false;
        let mut report = ModuleReport {
            name: module.name.clone(),
            item_id: module.item_id,
            requested_hz: module_config.sample_rate_hz,
            achieved_hz: None,
            divisor: None,
        };

        if let Some(requested_hz) = module_config.sample_rate_hz {
            let master = master_rate_hz.ok_or_else(|| {
                Error::Config(format!(
                    "module '{}' requests a sample rate but system.master_sampling_rate is not set",
                    module.name
                ))
            })?;
            // Substring match: high-rate module modes rename the setting
            // (e.g. "High Sample Rate"), and the vendor's own scripts match
            // by containment.
            let (rate_name, rate_setting) =
                find_rate_setting(&doc["Settings"]).ok_or_else(|| {
                    Error::Config(format!(
                        "module '{}' has no Sample Rate setting (is the module Disabled?)",
                        module.name
                    ))
                })?;
            let snapped = snap_sample_rate(&rate_setting, master, requested_hz)?;
            set_value(
                &mut doc["Settings"],
                &rate_name,
                &SettingValue::Number(snapped.enum_id as f64),
            )?;
            report.achieved_hz = Some(snapped.achieved_hz);
            report.divisor = Some(snapped.divisor);
            dirty = true;
        }

        for (name, value) in &module_config.settings {
            set_value(&mut doc["Settings"], name, value)?;
            dirty = true;
        }

        // Pair-mode modules (THM427): channel modes are module-level settings
        // ("Channel 1 and 2 Operation Mode"), not channel op-mode PUTs.
        let pair_assignments = pair_mode_assignments(module_config, &doc["Settings"])?;
        let pair_handled = !pair_assignments.is_empty();
        for (pair_name, mode) in pair_assignments {
            set_value(&mut doc["Settings"], &pair_name, &SettingValue::Text(mode))?;
            dirty = true;
        }

        if dirty {
            self.rest.put_item_settings(module.item_id, &doc).await?;
        }
        // Effective rate even when no rate was requested (needed for
        // per-channel timestamp math downstream).
        if report.achieved_hz.is_none()
            && let Some(master) = master_rate_hz
            && let Some((_, setting)) = find_rate_setting(&doc["Settings"])
            && let Some(divisor) = current_enum_numeric(&setting)
        {
            report.achieved_hz = Some(master / divisor);
            report.divisor = Some(divisor);
        }
        Ok((report, pair_handled))
    }

    async fn reconcile_channels(
        &self,
        module_config: &ModuleConfig,
        module: &DeviceModule,
        pair_handled: bool,
        module_rate_hz: Option<f64>,
    ) -> Result<Vec<ChannelReport>> {
        let mut reports = Vec::new();
        for channel_config in &module_config.channels {
            let item_id = *module
                .channel_ids
                .get(channel_config.index.checked_sub(1).ok_or_else(|| {
                    Error::Config("channel index is 1-based; 0 is invalid".into())
                })?)
                .ok_or_else(|| {
                    Error::Config(format!(
                        "module '{}' has {} channels; index {} out of range",
                        module.name,
                        module.channel_ids.len(),
                        channel_config.index
                    ))
                })?;

            if let (Some(mode), false) = (&channel_config.mode, pair_handled) {
                let mut op_mode = self.rest.item_operation_mode(item_id).await?;
                let setting = op_mode["Settings"]
                    .as_array()
                    .and_then(|s| s.first())
                    .cloned()
                    .ok_or_else(|| {
                        Error::Stream("operation mode document has no Settings".into())
                    })?;
                let id = resolve_enum_id(&setting, mode)?;
                op_mode["Settings"][0]["Value"] = id.into();
                self.rest.put_item_operation_mode(item_id, &op_mode).await?;
            }

            // Streaming State is declarative (PLAN.md D8): declared channels
            // are driven to Enabled or Disabled per the config, guarded by
            // the entry's presence (outputs/CAN-tx may not carry one).
            let mut doc = self.rest.item_settings(item_id).await?;
            let mut dirty = false;
            for (name, value) in &channel_config.settings {
                set_value(&mut doc["Settings"], name, value)?;
                dirty = true;
            }
            if find_setting(&doc["Data"], "Streaming State").is_some() {
                let target = if channel_config.streaming {
                    "Enabled"
                } else {
                    "Disabled"
                };
                if channel_config.streaming || streaming_state_enabled(&doc["Data"]) {
                    set_value(
                        &mut doc["Data"],
                        "Streaming State",
                        &SettingValue::Text(target.into()),
                    )?;
                    dirty = true;
                }
            }
            if dirty {
                self.rest.put_item_settings(item_id, &doc).await?;
            }

            reports.push(ChannelReport {
                alias: channel_config.display_name(),
                item_id,
                mode: channel_config.mode.clone(),
                streaming: channel_config.streaming,
                sample_rate_hz: module_rate_hz,
                dbc: channel_config.dbc.clone(),
                pulses_per_rev: channel_config.pulses_per_rev.unwrap_or(1.0),
            });
        }

        // Undeclared channels of a DECLARED module: read-before-write disable
        // of any leftover streaming state (settings persist across power
        // cycles; a channel enabled by a previous session would otherwise
        // stream unidentifiable data). Channels of undeclared modules are
        // left untouched — instro owns only the modules it declares.
        let declared: Vec<i64> = reports.iter().map(|r| r.item_id).collect();
        for &item_id in &module.channel_ids {
            if declared.contains(&item_id) {
                continue;
            }
            let mut doc = self.rest.item_settings(item_id).await?;
            if streaming_state_enabled(&doc["Data"]) {
                set_value(
                    &mut doc["Data"],
                    "Streaming State",
                    &SettingValue::Text("Disabled".into()),
                )?;
                self.rest.put_item_settings(item_id, &doc).await?;
            }
        }
        Ok(reports)
    }
}

/// True when the Data array has a Streaming State entry whose current Value
/// resolves to the "Enabled" SupportedValue.
fn streaming_state_enabled(data_array: &Value) -> bool {
    let Some(setting) = find_setting(data_array, "Streaming State") else {
        return false;
    };
    let Some(current) = setting.get("Value").and_then(Value::as_i64) else {
        return false;
    };
    setting
        .get("SupportedValues")
        .and_then(Value::as_array)
        .and_then(|vs| {
            vs.iter()
                .find(|v| v.get("Id").and_then(Value::as_i64) == Some(current))
        })
        .and_then(|v| v.get("Description").and_then(Value::as_str))
        .is_some_and(|d| d.eq_ignore_ascii_case("Enabled"))
}

/// Find the module's sample-rate setting by name containment ("Sample Rate",
/// "High Sample Rate", ...) — vendor scripts match by substring because
/// high-rate modes rename the setting.
fn find_rate_setting(settings: &Value) -> Option<(String, Value)> {
    settings.as_array()?.iter().find_map(|s| {
        let name = s.get("Name").and_then(Value::as_str)?;
        name.contains("Sample Rate")
            .then(|| (name.to_string(), s.clone()))
    })
}

fn find_module(tree: &DeviceTree, module_config: &ModuleConfig) -> Result<DeviceModule> {
    tree.modules
        .iter()
        .filter(|m| m.name == module_config.name)
        .nth(module_config.occurrence)
        .cloned()
        .ok_or_else(|| {
            let present: Vec<&str> = tree.modules.iter().map(|m| m.name.as_str()).collect();
            Error::Config(format!(
                "module '{}' (occurrence {}) not found on device; present: {present:?}",
                module_config.name, module_config.occurrence
            ))
        })
}

/// For modules whose settings include "Channel A and B Operation Mode" entries,
/// map configured channel modes onto those pair settings. Errors when two
/// channels of one pair declare different modes (the hardware constraint).
fn pair_mode_assignments(
    module_config: &ModuleConfig,
    module_settings: &Value,
) -> Result<Vec<(String, String)>> {
    let pair_settings: Vec<(String, [usize; 2])> = module_settings
        .as_array()
        .map(|settings| {
            settings
                .iter()
                .filter_map(|s| {
                    let name = s.get("Name").and_then(Value::as_str)?;
                    Some((name.to_string(), parse_pair_setting_name(name)?))
                })
                .collect()
        })
        .unwrap_or_default();
    if pair_settings.is_empty() {
        return Ok(Vec::new());
    }

    let mut assignments: BTreeMap<String, (String, String)> = BTreeMap::new();
    for channel in &module_config.channels {
        let Some(mode) = &channel.mode else { continue };
        let Some((pair_name, _)) = pair_settings
            .iter()
            .find(|(_, positions)| positions.contains(&channel.index))
        else {
            return Err(Error::Config(format!(
                "channel index {} has no matching pair setting on module '{}'",
                channel.index, module_config.name
            )));
        };
        match assignments.get(pair_name) {
            Some((existing_mode, other)) if existing_mode != mode => {
                return Err(Error::Config(format!(
                    "'{}' and '{}' share pair setting '{pair_name}' but declare different \
                     modes ('{existing_mode}' vs '{mode}'); paired channels must use the same mode",
                    other,
                    channel.display_name(),
                )));
            }
            _ => {
                assignments.insert(pair_name.clone(), (mode.clone(), channel.display_name()));
            }
        }
    }
    Ok(assignments
        .into_iter()
        .map(|(name, (mode, _))| (name, mode))
        .collect())
}

fn parse_pair_setting_name(name: &str) -> Option<[usize; 2]> {
    let rest = name.strip_prefix("Channel ")?;
    let rest = rest.strip_suffix(" Operation Mode")?;
    let (first, second) = rest.split_once(" and ")?;
    Some([first.trim().parse().ok()?, second.trim().parse().ok()?])
}

/// The `Numeric` of the currently-selected SupportedValue, falling back to
/// parsing a leading number from its description (e.g. "131072 Hz").
fn current_enum_numeric(setting: &Value) -> Option<f64> {
    let value = setting.get("Value").and_then(Value::as_i64)?;
    let chosen = setting
        .get("SupportedValues")
        .and_then(Value::as_array)?
        .iter()
        .find(|v| v.get("Id").and_then(Value::as_i64) == Some(value))?;
    chosen.get("Numeric").and_then(Value::as_f64).or_else(|| {
        chosen
            .get("Description")
            .and_then(Value::as_str)?
            .split_whitespace()
            .next()?
            .parse()
            .ok()
    })
}

/// Build the module/channel tree from the flat `/item/list` order.
fn parse_device_tree(items: &[Value]) -> Result<DeviceTree> {
    let mut controller_id = None;
    let mut modules: Vec<DeviceModule> = Vec::new();
    for item in items {
        let item_id = item
            .get("ItemId")
            .and_then(Value::as_i64)
            .ok_or_else(|| Error::Stream("item list entry missing ItemId".into()))?;
        let name = item
            .get("ItemName")
            .and_then(Value::as_str)
            .unwrap_or_default();
        match item.get("ItemType").and_then(Value::as_str) {
            Some("Controller") => controller_id = controller_id.or(Some(item_id)),
            Some("Module") | Some("External Module") => modules.push(DeviceModule {
                item_id,
                name: name.to_string(),
                channel_ids: Vec::new(),
            }),
            Some("Channel") => {
                if let Some(module) = modules.last_mut() {
                    // Channels carry their module's name, bare ("WSB42X2") or
                    // with a role suffix ("TAC221 Tacho", "XMC237 CAN FD") —
                    // both confirmed on a real MicroQ (Q2.4.11). Anything else
                    // means the flat-order adjacency assumption (assumptions.md
                    // A21) does not hold on this firmware — fail loudly rather
                    // than configure the wrong physical channel.
                    let carries_module_name = name
                        .strip_prefix(module.name.as_str())
                        .is_some_and(|rest| rest.is_empty() || rest.starts_with(' '));
                    if !carries_module_name {
                        return Err(Error::Stream(format!(
                            "channel item {item_id} ('{name}') does not carry the name of its \
                             preceding module '{}': /item/list ordering violates the \
                             module->channel adjacency assumption (A21)",
                            module.name
                        )));
                    }
                    module.channel_ids.push(item_id);
                }
            }
            _ => {}
        }
    }
    Ok(DeviceTree {
        controller_id: controller_id
            .ok_or_else(|| Error::Stream("no Controller in item list".into()))?,
        modules,
    })
}

fn parse_major(version: &str) -> Option<u32> {
    let digits: String = version
        .chars()
        .skip_while(|c| !c.is_ascii_digit())
        .take_while(|c| c.is_ascii_digit())
        .collect();
    digits.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_major_version() {
        assert_eq!(parse_major("Q2.4.15"), Some(2));
        assert_eq!(parse_major("1.4.11"), Some(1));
        assert_eq!(parse_major("nope"), None);
    }

    #[test]
    fn parses_pair_setting_names() {
        assert_eq!(
            parse_pair_setting_name("Channel 7 and 8 Operation Mode"),
            Some([7, 8])
        );
        assert_eq!(parse_pair_setting_name("Sample Rate"), None);
    }

    fn item(id: i64, name: &str, item_type: &str) -> Value {
        serde_json::json!({ "ItemId": id, "ItemName": name, "ItemType": item_type })
    }

    /// Item list captured from a real MicroQ (Q2.4.11, 2026-07-22): built-in
    /// modules sit under the controller ahead of the SC, their channels carry
    /// role suffixes, ItemId 3 does not exist, and empty G2 slots are modules.
    #[test]
    fn parses_microq_item_list_with_role_suffixed_channels() {
        let items = vec![
            item(1, "MicroQ", "Controller"),
            item(2, "XMC237", "Module"),
            item(4, "XMC237 GPS", "Channel"),
            item(5, "XMC237 CAN FD", "Channel"),
            item(6, "XMC237 CAN FD", "Channel"),
            item(7, "SC10", "SignalConditioner"),
            item(8, "WSB42X2", "Module"),
            item(9, "WSB42X2", "Channel"),
            item(10, "WSB42X2", "Channel"),
            item(11, "WSB42X2", "Channel"),
            item(12, "WSB42X2", "Channel"),
            item(13, "TAC221", "Module"),
            item(14, "TAC221 Tacho", "Channel"),
            item(15, "TAC221 Scope", "Channel"),
            item(16, "TAC221 Tacho", "Channel"),
            item(17, "TAC221 Scope", "Channel"),
            item(18, "Empty", "Module"),
            item(19, "Empty", "Module"),
        ];
        let tree = parse_device_tree(&items).unwrap();
        assert_eq!(tree.controller_id, 1);
        let by_name: Vec<(&str, &[i64])> = tree
            .modules
            .iter()
            .map(|m| (m.name.as_str(), m.channel_ids.as_slice()))
            .collect();
        assert_eq!(
            by_name,
            vec![
                ("XMC237", &[4, 5, 6][..]),
                ("WSB42X2", &[9, 10, 11, 12][..]),
                ("TAC221", &[14, 15, 16, 17][..]),
                ("Empty", &[][..]),
                ("Empty", &[][..]),
            ]
        );
    }

    #[test]
    fn rejects_channel_not_carrying_module_name() {
        for channel_name in ["ICS425", "WSB42X25"] {
            let items = vec![
                item(1, "MicroQ", "Controller"),
                item(2, "WSB42X2", "Module"),
                item(3, channel_name, "Channel"),
            ];
            let error = parse_device_tree(&items).unwrap_err().to_string();
            assert!(error.contains("A21"), "unexpected error: {error}");
        }
    }
}
