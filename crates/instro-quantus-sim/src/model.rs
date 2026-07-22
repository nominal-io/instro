//! Item tree state and JSON assembly for the QServer Q2.x REST envelope.
//!
//! Response shapes follow the Q2 migration examples in the manual
//! (docs/api-notes.md; docs/assumptions.md tracks the guessed parts). Settings
//! writes are cached (`settings_applied = false`) until
//! `PUT /system/settings/apply`, mirroring the device's two-phase commit.

use serde_json::{Value, json};

pub const ITEM_TYPE_CONTROLLER: (&str, i64) = ("Controller", 0);
pub const ITEM_TYPE_SIGNAL_CONDITIONER: (&str, i64) = ("SignalConditioner", 1);
pub const ITEM_TYPE_MODULE: (&str, i64) = ("Module", 2);
pub const ITEM_TYPE_CHANNEL: (&str, i64) = ("Channel", 4);

/// One operation mode an item supports, with the Settings document that mode exposes.
#[derive(Debug, Clone)]
pub struct OpMode {
    pub id: i64,
    pub description: String,
    /// Default `Settings` array for this mode (each entry: Name/Type/SupportedValues/Value).
    pub settings: Value,
}

#[derive(Debug, Clone)]
pub struct ItemState {
    pub item_id: i64,
    pub item_name: String,
    pub item_name_identifier: i64,
    pub item_type: &'static str,
    pub item_type_identifier: i64,
    /// `Info` array (e.g. serial number entries).
    pub info: Value,
    pub modes: Vec<OpMode>,
    pub current_mode: i64,
    /// Currently cached `Settings` array (starts as the default mode's defaults).
    pub settings: Value,
    /// Cached `Data` array (Streaming State / Local Storage State); empty array
    /// for items that cannot stream.
    pub data: Value,
    pub settings_applied: bool,
    pub children: Vec<usize>,
    /// For module items: index into `config.slots` this module came from.
    pub slot_index: Option<usize>,
    /// ItemIds to skip after this item (real firmware reserves ids for
    /// channels it does not expose, e.g. the XMC237's unfitted ICP channel).
    pub hidden_ids_after: i64,
}

impl ItemState {
    fn mode(&self, id: i64) -> Option<&OpMode> {
        self.modes.iter().find(|m| m.id == id)
    }

    fn current_mode_json(&self) -> Value {
        let description = self
            .mode(self.current_mode)
            .map(|m| m.description.clone())
            .unwrap_or_default();
        json!({ "Description": description, "Id": self.current_mode })
    }

    fn envelope(&self) -> Value {
        json!({
            "ItemId": self.item_id,
            "ItemName": self.item_name,
            "ItemNameIdentifier": self.item_name_identifier,
            "ItemType": self.item_type,
            "ItemTypeIdentifier": self.item_type_identifier,
            "Info": self.info,
        })
    }
}

/// Channel ItemNameIdentifiers of CAN FD channels (CAN42S2, XMC237, XMC100).
pub const CAN_CHANNEL_IDENTIFIERS: [i64; 3] = [0, 34, 44];

/// The whole simulated device: a flat arena of items (index 0 = controller root)
/// plus stream-epoch bookkeeping.
#[derive(Debug)]
pub struct SimState {
    pub items: Vec<ItemState>,
    /// Incremented by every apply that restarts the measurement; the stream
    /// plane (Phase 3) resets sequence numbers/timestamps when it changes.
    pub epoch: u64,
    /// Cached CAN transmit message lists, keyed by CAN channel ItemId.
    pub can_message_lists: std::collections::HashMap<i64, Value>,
    /// Transmit counter per CAN channel ItemId (introspection for tests).
    pub can_transmits: std::collections::HashMap<i64, u64>,
}

pub enum ApplyOutcome {
    Applied,
    AppliedWithRestart,
}

impl SimState {
    pub fn find(&self, item_id: i64) -> Option<usize> {
        self.items.iter().position(|i| i.item_id == item_id)
    }

    /// `GET /item/list` — flat array in tree order.
    pub fn item_list_json(&self) -> Value {
        Value::Array(
            self.items
                .iter()
                .map(|i| {
                    json!({
                        "ItemId": i.item_id,
                        "ItemName": i.item_name,
                        "ItemNameIdentifier": i.item_name_identifier,
                        "ItemType": i.item_type,
                        "ItemTypeIdentifier": i.item_type_identifier,
                    })
                })
                .collect(),
        )
    }

    /// `GET /item/settings/?itemId=` — full envelope with cached settings.
    pub fn item_settings_json(&self, idx: usize) -> Value {
        let item = &self.items[idx];
        let mut doc = item.envelope();
        let obj = doc.as_object_mut().unwrap();
        obj.insert("OperationMode".into(), item.current_mode_json());
        obj.insert("SettingsApplied".into(), json!(item.settings_applied));
        obj.insert("Settings".into(), item.settings.clone());
        if !item.data.as_array().map(Vec::is_empty).unwrap_or(true) {
            obj.insert("Data".into(), item.data.clone());
        }
        doc
    }

    /// `GET /item/operationMode/?itemId=` — envelope with a single
    /// "Operation Mode" enumeration setting.
    pub fn item_op_mode_json(&self, idx: usize) -> Value {
        let item = &self.items[idx];
        let supported: Vec<Value> = item
            .modes
            .iter()
            .map(|m| json!({ "Id": m.id, "Description": m.description }))
            .collect();
        let mut doc = item.envelope();
        let obj = doc.as_object_mut().unwrap();
        obj.insert("SettingsApplied".into(), json!(item.settings_applied));
        obj.insert(
            "Settings".into(),
            json!([{
                "Name": "Operation Mode",
                "Type": "Enumeration",
                "SupportedValues": supported,
                "Value": item.current_mode,
            }]),
        );
        doc
    }

    /// `GET /system/settings` — the settings tree from the controller down.
    pub fn system_settings_json(&self) -> Value {
        self.subtree_json(0)
    }

    fn subtree_json(&self, idx: usize) -> Value {
        let mut doc = self.item_settings_json(idx);
        let children: Vec<Value> = self.items[idx]
            .children
            .iter()
            .map(|&c| self.subtree_json(c))
            .collect();
        if !children.is_empty() {
            doc.as_object_mut()
                .unwrap()
                .insert("Children".into(), Value::Array(children));
        }
        doc
    }

    /// `PUT /item/settings/?itemId=` — cache new Settings/Data values. Values are
    /// validated against each setting's SupportedValues for enumerations.
    pub fn put_item_settings(&mut self, idx: usize, doc: &Value) -> Result<(), String> {
        let incoming_settings = doc.get("Settings").and_then(Value::as_array).cloned();
        let incoming_data = doc.get("Data").and_then(Value::as_array).cloned();
        let item = &mut self.items[idx];

        if let Some(incoming) = incoming_settings {
            merge_values(&mut item.settings, &incoming, "Settings")?;
        }
        if let Some(incoming) = incoming_data {
            merge_values(&mut item.data, &incoming, "Data")?;
        }
        item.settings_applied = false;
        self.propagate_pair_modes(idx);
        Ok(())
    }

    /// Modules like the THM427 expose channel modes as module-level pair
    /// settings ("Channel 1 and 2 Operation Mode"); the manual says channel
    /// operation modes are "modified by accessing the Module settings", so a
    /// module settings write switches the paired child channels' modes.
    fn propagate_pair_modes(&mut self, module_idx: usize) {
        let children = self.items[module_idx].children.clone();
        let settings = match self.items[module_idx].settings.as_array() {
            Some(settings) => settings.clone(),
            None => return,
        };
        for setting in &settings {
            let name = setting.get("Name").and_then(Value::as_str).unwrap_or("");
            let Some(positions) = parse_pair_setting_name(name) else {
                continue;
            };
            let Some(chosen_id) = setting.get("Value").and_then(Value::as_i64) else {
                continue;
            };
            let Some(description) = setting
                .get("SupportedValues")
                .and_then(Value::as_array)
                .and_then(|values| {
                    values
                        .iter()
                        .find(|v| v.get("Id").and_then(Value::as_i64) == Some(chosen_id))
                })
                .and_then(|v| v.get("Description"))
                .and_then(Value::as_str)
                .map(str::to_string)
            else {
                continue;
            };
            for position in positions {
                let Some(&child_idx) = children.get(position - 1) else {
                    continue;
                };
                let child = &mut self.items[child_idx];
                let Some(mode) = child.modes.iter().find(|m| m.description == description) else {
                    continue;
                };
                if child.current_mode != mode.id {
                    child.settings = mode.settings.clone();
                    child.current_mode = mode.id;
                    child.settings_applied = false;
                }
            }
        }
    }

    /// `PUT /item/operationMode/?itemId=` — switch mode; the item's settings
    /// document is replaced by the new mode's defaults (assumption A7).
    pub fn put_item_op_mode(&mut self, idx: usize, doc: &Value) -> Result<(), String> {
        let value = doc
            .get("Settings")
            .and_then(Value::as_array)
            .and_then(|s| s.first())
            .and_then(|s| s.get("Value"))
            .and_then(Value::as_i64)
            .ok_or_else(|| "Operation mode document missing Settings[0].Value".to_string())?;

        let item = &mut self.items[idx];
        let mode = item
            .mode(value)
            .ok_or_else(|| format!("Unsupported operation mode id {value}"))?
            .clone();
        if item.current_mode != value {
            item.settings = mode.settings.clone();
            item.current_mode = value;
        }
        item.settings_applied = false;
        Ok(())
    }

    /// `PUT /system/settings/apply` — mark everything applied. Reports a
    /// measurement restart (and bumps the epoch) if anything was pending, since
    /// Phase 1 doesn't yet distinguish restart-triggering settings (A9).
    pub fn apply(&mut self) -> ApplyOutcome {
        let any_pending = self.items.iter().any(|i| !i.settings_applied);
        for item in &mut self.items {
            item.settings_applied = true;
        }
        if any_pending {
            self.epoch += 1;
            ApplyOutcome::AppliedWithRestart
        } else {
            ApplyOutcome::Applied
        }
    }
}

/// Parse `"Channel 1 and 2 Operation Mode"` into 1-based channel positions.
fn parse_pair_setting_name(name: &str) -> Option<[usize; 2]> {
    let rest = name.strip_prefix("Channel ")?;
    let rest = rest.strip_suffix(" Operation Mode")?;
    let (first, second) = rest.split_once(" and ")?;
    Some([first.trim().parse().ok()?, second.trim().parse().ok()?])
}

/// Merge incoming Name/Value pairs into the stored settings array, validating
/// enumeration values against SupportedValues. Unknown names are rejected.
fn merge_values(stored: &mut Value, incoming: &[Value], what: &str) -> Result<(), String> {
    let stored_arr = stored
        .as_array_mut()
        .ok_or_else(|| format!("item has no {what}"))?;
    for entry in incoming {
        let name = entry
            .get("Name")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("{what} entry missing Name"))?;
        let value = entry
            .get("Value")
            .ok_or_else(|| format!("{what} entry '{name}' missing Value"))?;
        let target = stored_arr
            .iter_mut()
            .find(|s| s.get("Name").and_then(Value::as_str) == Some(name))
            .ok_or_else(|| format!("unknown {what} entry '{name}'"))?;

        if let Some(supported) = target.get("SupportedValues").and_then(Value::as_array) {
            let id = value
                .as_i64()
                .ok_or_else(|| format!("enumeration '{name}' requires an integer Id"))?;
            if !supported
                .iter()
                .any(|v| v.get("Id").and_then(Value::as_i64) == Some(id))
            {
                return Err(format!("value {id} out of range for '{name}'"));
            }
        }
        target
            .as_object_mut()
            .unwrap()
            .insert("Value".into(), value.clone());
    }
    Ok(())
}
