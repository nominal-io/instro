//! Build the simulated item tree from the rack config and embedded module
//! templates. Enum Ids and identifiers come from Mecalc's vendor-generated
//! QProtocolCSharp where known; docs/assumptions.md tracks the guessed parts.

use crate::config::SimConfig;
use crate::model::{
    ITEM_TYPE_CHANNEL, ITEM_TYPE_CONTROLLER, ITEM_TYPE_MODULE, ITEM_TYPE_SIGNAL_CONDITIONER,
    ItemState, OpMode, SimState,
};
use serde_json::{Value, json};

const ICS425: &str = include_str!("../templates/ics425.json");
const THM427: &str = include_str!("../templates/thm427.json");
const MIC42X7: &str = include_str!("../templates/mic42x7.json");
const WSB42X2: &str = include_str!("../templates/wsb42x2.json");
const CAN42S2: &str = include_str!("../templates/can42s2.json");
const ICT42S6: &str = include_str!("../templates/ict42s6.json");
const ALO42S4: &str = include_str!("../templates/alo42s4.json");

/// (module name, template, module ItemNameIdentifier override, channel override).
/// ICS421 shares the ICS425 template but has its own identifiers (QProtocolCSharp:
/// ModuleType ICS421=213, ChannelType ICS421=13).
const MODULES: &[(&str, &str, Option<i64>, Option<i64>)] = &[
    ("ICS425", ICS425, None, None),
    ("ICS421", ICS425, Some(213), Some(13)),
    ("THM427", THM427, None, None),
    ("MIC42X7", MIC42X7, None, None),
    ("WSB42X2", WSB42X2, None, None),
    ("CAN42S2", CAN42S2, None, None),
    ("ICT42S6", ICT42S6, None, None),
    ("ALO42S4", ALO42S4, None, None),
];

fn controller_identifier(chassis: &str) -> Result<i64, String> {
    match chassis {
        "PQ20G2" => Ok(30080),
        "PQ30G2" => Ok(30090),
        "MicroQ" => Ok(30100),
        "PQ45" => Ok(30110),
        other => Err(format!(
            "unknown chassis '{other}' (supported: PQ20G2, PQ30G2, MicroQ, PQ45)"
        )),
    }
}

fn master_rate_id(rate_hz: u32) -> Result<i64, String> {
    match rate_hz {
        131072 => Ok(0),
        160000 => Ok(1),
        163840 => Ok(2),
        176400 => Ok(3),
        192000 => Ok(4),
        200000 => Ok(5),
        204800 => Ok(6),
        other => Err(format!("unsupported master sampling rate {other} Hz")),
    }
}

fn controller_settings(master_rate_hz: u32) -> Result<Value, String> {
    Ok(json!([
        {
            "Name": "Master Sampling Rate",
            "Type": "Enumeration",
            "SupportedValues": [
                { "Id": 0, "Description": "131072 Hz", "Numeric": 131072.0 },
                { "Id": 1, "Description": "160000 Hz", "Numeric": 160000.0 },
                { "Id": 2, "Description": "163840 Hz", "Numeric": 163840.0 },
                { "Id": 3, "Description": "176400 Hz", "Numeric": 176400.0 },
                { "Id": 4, "Description": "192000 Hz", "Numeric": 192000.0 },
                { "Id": 5, "Description": "200000 Hz", "Numeric": 200000.0 },
                { "Id": 6, "Description": "204800 Hz", "Numeric": 204800.0 }
            ],
            "Value": master_rate_id(master_rate_hz)?
        },
        {
            "Name": "Analog Data Streaming Format",
            "Type": "Enumeration",
            "SupportedValues": [
                { "Id": 0, "Description": "Processed" },
                { "Id": 1, "Description": "Raw" }
            ],
            "Value": 0
        }
    ]))
}

fn parse_modes(modes_json: &Value) -> Result<Vec<OpMode>, String> {
    modes_json
        .as_array()
        .ok_or("OperationModes must be an array")?
        .iter()
        .map(|m| {
            Ok(OpMode {
                id: m
                    .get("Id")
                    .and_then(Value::as_i64)
                    .ok_or("mode missing Id")?,
                description: m
                    .get("Description")
                    .and_then(Value::as_str)
                    .ok_or("mode missing Description")?
                    .to_string(),
                settings: m.get("Settings").cloned().unwrap_or_else(|| json!([])),
            })
        })
        .collect()
}

/// Flip a Data array's "Streaming State" entry to its Enabled id.
fn set_streaming_enabled(data: &mut Value) {
    let Some(entries) = data.as_array_mut() else {
        return;
    };
    for entry in entries {
        if entry.get("Name").and_then(Value::as_str) != Some("Streaming State") {
            continue;
        }
        let enabled_id = entry
            .get("SupportedValues")
            .and_then(Value::as_array)
            .and_then(|vs| {
                vs.iter()
                    .find(|v| v.get("Description").and_then(Value::as_str) == Some("Enabled"))
            })
            .and_then(|v| v.get("Id").cloned());
        if let Some(id) = enabled_id {
            entry["Value"] = id;
        }
    }
}

fn item_from_modes(
    item_name: &str,
    item_name_identifier: i64,
    item_type: (&'static str, i64),
    info: Value,
    modes: Vec<OpMode>,
    default_mode: i64,
    data: Value,
) -> Result<ItemState, String> {
    let settings = modes
        .iter()
        .find(|m| m.id == default_mode)
        .map(|m| m.settings.clone())
        .ok_or_else(|| format!("default mode {default_mode} not in mode list for {item_name}"))?;
    Ok(ItemState {
        item_id: 0, // assigned during tree assembly
        item_name: item_name.to_string(),
        item_name_identifier,
        item_type: item_type.0,
        item_type_identifier: item_type.1,
        info,
        modes,
        current_mode: default_mode,
        settings,
        data,
        settings_applied: true,
        children: Vec::new(),
    })
}

/// Build the full item tree: Controller -> SC42 -> Modules -> Channels, with
/// ItemIds assigned sequentially in depth-first order starting at 1.
pub fn build_state(config: &SimConfig) -> Result<SimState, String> {
    let mut items: Vec<ItemState> = Vec::new();

    let controller = item_from_modes(
        &config.system.chassis,
        controller_identifier(&config.system.chassis)?,
        ITEM_TYPE_CONTROLLER,
        json!([{ "Name": "SerialNumber", "Value": config.system.serial }]),
        vec![
            OpMode {
                id: 0,
                description: "Disabled".into(),
                settings: json!([]),
            },
            OpMode {
                id: 1,
                description: "Enabled".into(),
                settings: controller_settings(config.system.master_sampling_rate)?,
            },
        ],
        1,
        json!([]),
    )?;
    items.push(controller);

    let sc = item_from_modes(
        "SC42",
        10070,
        ITEM_TYPE_SIGNAL_CONDITIONER,
        json!([{ "Name": "SerialNumber", "Value": format!("{}-SC", config.system.serial) }]),
        vec![OpMode {
            id: 1,
            description: "Enabled".into(),
            settings: json!([]),
        }],
        1,
        json!([]),
    )?;
    let sc_idx = items.len();
    items.push(sc);
    items[0].children.push(sc_idx);

    for slot in &config.slots {
        let (_, template_src, module_ident, channel_ident) = MODULES
            .iter()
            .find(|(name, ..)| *name == slot.module)
            .ok_or_else(|| {
                let known: Vec<&str> = MODULES.iter().map(|(n, ..)| *n).collect();
                format!(
                    "no template for module '{}' yet (available: {})",
                    slot.module,
                    known.join(", ")
                )
            })?;
        let template: Value =
            serde_json::from_str(template_src).map_err(|e| format!("template parse: {e}"))?;

        let module = item_from_modes(
            &slot.module,
            module_ident.unwrap_or_else(|| {
                template
                    .get("ItemNameIdentifier")
                    .and_then(Value::as_i64)
                    .unwrap_or(0)
            }),
            ITEM_TYPE_MODULE,
            json!([{ "Name": "SerialNumber", "Value": format!("SIM{:04}", slot.slot) }]),
            parse_modes(
                template
                    .get("OperationModes")
                    .ok_or("template missing OperationModes")?,
            )?,
            slot.boot_mode.unwrap_or_else(|| {
                template
                    .get("DefaultOperationMode")
                    .and_then(Value::as_i64)
                    .unwrap_or(1)
            }),
            json!([]),
        )?;
        let module_idx = items.len();
        items.push(module);
        items[sc_idx].children.push(module_idx);

        let mut position = 0usize; // 1-based channel index within the module
        for group in template
            .get("Channels")
            .and_then(Value::as_array)
            .ok_or("template missing Channels")?
        {
            let count = group.get("Count").and_then(Value::as_u64).unwrap_or(1);
            for _ in 0..count {
                position += 1;
                let mut channel = item_from_modes(
                    &slot.module,
                    channel_ident.unwrap_or_else(|| {
                        group
                            .get("ItemNameIdentifier")
                            .and_then(Value::as_i64)
                            .unwrap_or(0)
                    }),
                    ITEM_TYPE_CHANNEL,
                    json!([]),
                    parse_modes(
                        group
                            .get("OperationModes")
                            .ok_or("channel group missing OperationModes")?,
                    )?,
                    group
                        .get("DefaultOperationMode")
                        .and_then(Value::as_i64)
                        .unwrap_or(1),
                    group.get("Data").cloned().unwrap_or_else(|| json!([])),
                )?;
                if slot
                    .channels
                    .iter()
                    .any(|c| c.index == position && c.boot_streaming)
                {
                    set_streaming_enabled(&mut channel.data);
                }
                let channel_idx = items.len();
                items.push(channel);
                items[module_idx].children.push(channel_idx);
            }
        }
    }

    for (i, item) in items.iter_mut().enumerate() {
        item.item_id = (i + 1) as i64;
    }

    Ok(SimState {
        items,
        epoch: 0,
        can_message_lists: Default::default(),
        can_transmits: Default::default(),
    })
}
