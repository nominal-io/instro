//! Settings-document helpers: find settings by name, resolve enumeration
//! values by description, snap sample rates to achievable divisors.

use crate::config::SettingValue;
use crate::error::{Error, Result};
use serde_json::Value;

/// Find a setting entry by exact name in a Settings/Data array.
pub fn find_setting<'a>(array: &'a Value, name: &str) -> Option<&'a Value> {
    array
        .as_array()?
        .iter()
        .find(|s| s.get("Name").and_then(Value::as_str) == Some(name))
}

fn setting_names(array: &Value) -> Vec<String> {
    array
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|s| s.get("Name").and_then(Value::as_str))
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// Resolve an enumeration description to its integer Id (exact match first,
/// then case-insensitive).
pub fn resolve_enum_id(setting: &Value, description: &str) -> Result<i64> {
    let supported = setting
        .get("SupportedValues")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            Error::Config(format!(
                "setting '{}' is not an enumeration",
                setting.get("Name").and_then(Value::as_str).unwrap_or("?")
            ))
        })?;
    let matched = supported
        .iter()
        .find(|v| v.get("Description").and_then(Value::as_str) == Some(description))
        .or_else(|| {
            supported.iter().find(|v| {
                v.get("Description")
                    .and_then(Value::as_str)
                    .is_some_and(|d| d.eq_ignore_ascii_case(description))
            })
        });
    match matched {
        Some(v) => Ok(v.get("Id").and_then(Value::as_i64).unwrap_or_default()),
        None => {
            let options: Vec<&str> = supported
                .iter()
                .filter_map(|v| v.get("Description").and_then(Value::as_str))
                .collect();
            Err(Error::Config(format!(
                "'{description}' is not a supported value for setting '{}'; options: {options:?}",
                setting.get("Name").and_then(Value::as_str).unwrap_or("?")
            )))
        }
    }
}

/// Set `name` in a Settings/Data array to `value`, resolving enum descriptions
/// and honoring ValidationLimits for numeric settings. Mutates the document.
pub fn set_value(array: &mut Value, name: &str, value: &SettingValue) -> Result<()> {
    let names = setting_names(array);
    let entry = array
        .as_array_mut()
        .and_then(|a| {
            a.iter_mut()
                .find(|s| s.get("Name").and_then(Value::as_str) == Some(name))
        })
        .ok_or_else(|| Error::Config(format!("no setting named '{name}'; available: {names:?}")))?;

    let is_enum = entry.get("SupportedValues").is_some();
    let new_value: Value = match (is_enum, value) {
        (true, SettingValue::Text(description)) => resolve_enum_id(entry, description)?.into(),
        (true, SettingValue::Number(n)) => {
            // Allow raw ids for enums, but only if the id exists.
            let id = *n as i64;
            let valid = entry
                .get("SupportedValues")
                .and_then(Value::as_array)
                .is_some_and(|vs| {
                    vs.iter()
                        .any(|v| v.get("Id").and_then(Value::as_i64) == Some(id))
                });
            if !valid {
                return Err(Error::Config(format!(
                    "id {id} is not a supported value for enumeration '{name}'"
                )));
            }
            id.into()
        }
        (false, SettingValue::Number(n)) => {
            if let Some(limits) = entry.get("ValidationLimits") {
                let lower = limits.get("Lower").and_then(Value::as_f64);
                let upper = limits.get("Upper").and_then(Value::as_f64);
                if lower.is_some_and(|l| *n < l) || upper.is_some_and(|u| *n > u) {
                    return Err(Error::Config(format!(
                        "value {n} outside limits [{lower:?}, {upper:?}] for setting '{name}'"
                    )));
                }
            }
            // Integral values go on the wire as JSON integers ("3", not
            // "3.0"): integer-typed settings on the embedded firmware may not
            // accept a float literal (the vendor client always emits ints).
            if n.fract() == 0.0 && *n >= i64::MIN as f64 && *n <= i64::MAX as f64 {
                (*n as i64).into()
            } else {
                (*n).into()
            }
        }
        (false, SettingValue::Text(text)) => text.clone().into(),
    };
    entry
        .as_object_mut()
        .unwrap()
        .insert("Value".into(), new_value);
    Ok(())
}

/// A snapped sample-rate choice for a module.
#[derive(Debug, Clone, PartialEq)]
pub struct SnappedRate {
    pub enum_id: i64,
    pub divisor: f64,
    pub achieved_hz: f64,
}

/// Snap a requested rate (Hz) to the nearest rate achievable from
/// `master_rate_hz` via the module's supported divisors. The divisor comes from
/// the SupportedValue's `Numeric` field, falling back to parsing
/// "MSR Divide by N" descriptions.
pub fn snap_sample_rate(
    sample_rate_setting: &Value,
    master_rate_hz: f64,
    requested_hz: f64,
) -> Result<SnappedRate> {
    let supported = sample_rate_setting
        .get("SupportedValues")
        .and_then(Value::as_array)
        .ok_or_else(|| Error::Config("Sample Rate setting has no SupportedValues".into()))?;

    let mut best: Option<SnappedRate> = None;
    for value in supported {
        let Some(id) = value.get("Id").and_then(Value::as_i64) else {
            continue;
        };
        let divisor = value
            .get("Numeric")
            .and_then(Value::as_f64)
            .or_else(|| {
                value
                    .get("Description")
                    .and_then(Value::as_str)
                    .and_then(|d| d.strip_prefix("MSR Divide by "))
                    .and_then(|n| n.trim().parse().ok())
            })
            .filter(|d| *d > 0.0);
        let Some(divisor) = divisor else { continue };
        let achieved_hz = master_rate_hz / divisor;
        let candidate = SnappedRate {
            enum_id: id,
            divisor,
            achieved_hz,
        };
        let better = match &best {
            None => true,
            Some(current) => {
                (achieved_hz - requested_hz).abs() < (current.achieved_hz - requested_hz).abs()
            }
        };
        if better {
            best = Some(candidate);
        }
    }
    best.ok_or_else(|| Error::Config("no parsable Sample Rate divisors on this module".into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rate_setting() -> Value {
        json!({
            "Name": "Sample Rate",
            "Type": "Enumeration",
            "SupportedValues": [
                { "Id": 0, "Description": "MSR Divide by 2", "Numeric": 2 },
                { "Id": 7, "Description": "MSR Divide by 256", "Numeric": 256 }
            ],
            "Value": 7
        })
    }

    #[test]
    fn snaps_to_exact_divisor() {
        let snapped = snap_sample_rate(&rate_setting(), 131072.0, 65536.0).unwrap();
        assert_eq!(snapped.enum_id, 0);
        assert_eq!(snapped.achieved_hz, 65536.0);
    }

    #[test]
    fn snaps_low_request_to_slowest_rate() {
        // The customer's 100 Sa/s case: slowest achievable is MSR/256 = 512.
        let snapped = snap_sample_rate(&rate_setting(), 131072.0, 100.0).unwrap();
        assert_eq!(snapped.enum_id, 7);
        assert_eq!(snapped.achieved_hz, 512.0);
    }

    #[test]
    fn snaps_from_description_when_numeric_missing() {
        let setting = json!({
            "Name": "Sample Rate",
            "SupportedValues": [{ "Id": 3, "Description": "MSR Divide by 8" }],
            "Value": 3
        });
        let snapped = snap_sample_rate(&setting, 204800.0, 20000.0).unwrap();
        assert_eq!(snapped.divisor, 8.0);
    }
}
