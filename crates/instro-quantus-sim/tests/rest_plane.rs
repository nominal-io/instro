//! REST-plane integration tests. The configure-flow test mirrors Mecalc's
//! ConfigureICS42.py step by step (including trailing slashes), so passing here
//! is a strong proxy for the Phase 1 exit criterion of running the vendor
//! script unmodified.

use instro_quantus_sim::config::SimConfig;
use instro_quantus_sim::rest::SimServer;
use serde_json::Value;

fn start_sim() -> (SimServer, String) {
    let config: SimConfig = toml::from_str(
        r#"
        [system]
        chassis = "MicroQ"
        serial = "SIM0001"
        master_sampling_rate = 131072

        [server]
        rest_port = 0
        stream_port = 0

        [[slots]]
        slot = 1
        module = "ICS425"

        [[slots]]
        slot = 2
        module = "THM427"
        "#,
    )
    .unwrap();
    let server = SimServer::start(config).unwrap();
    let base = format!("http://127.0.0.1:{}", server.rest_port());
    (server, base)
}

fn get_json(url: &str) -> Value {
    ureq::get(url).call().unwrap().into_json().unwrap()
}

fn put_json(url: &str, doc: &Value) -> Value {
    ureq::put(url).send_json(doc).unwrap().into_json().unwrap()
}

#[test]
fn ping_and_version() {
    let (_server, base) = start_sim();
    let ping = get_json(&format!("{base}/info/ping/"));
    assert_eq!(ping["Code"], 0);
    let version = get_json(&format!("{base}/version/"));
    assert!(version["Version"].as_str().unwrap().contains("2."));
}

#[test]
fn vendor_item_discovery() {
    // Mirrors ConfigureICS42.py: find the ICS425 module and exactly 6 channels
    // by ItemName + ItemType strings.
    let (_server, base) = start_sim();
    let items = get_json(&format!("{base}/item/list/"));
    let items = items.as_array().unwrap();

    let module_ids: Vec<i64> = items
        .iter()
        .filter(|i| i["ItemName"] == "ICS425" && i["ItemType"] == "Module")
        .map(|i| i["ItemId"].as_i64().unwrap())
        .collect();
    let channel_ids: Vec<i64> = items
        .iter()
        .filter(|i| i["ItemName"] == "ICS425" && i["ItemType"] == "Channel")
        .map(|i| i["ItemId"].as_i64().unwrap())
        .collect();
    assert_eq!(module_ids.len(), 1);
    assert_eq!(channel_ids.len(), 6);

    // Envelope fields the vendor client deserializes.
    let entry = items.iter().find(|i| i["ItemName"] == "ICS425").unwrap();
    for field in [
        "ItemId",
        "ItemName",
        "ItemNameIdentifier",
        "ItemType",
        "ItemTypeIdentifier",
    ] {
        assert!(entry.get(field).is_some(), "missing {field}");
    }
}

#[test]
fn vendor_configure_flow() {
    // Mirrors the full ConfigureICS42.py sequence against one channel.
    let (_server, base) = start_sim();
    let items = get_json(&format!("{base}/item/list/"));
    let channel_id = items
        .as_array()
        .unwrap()
        .iter()
        .find(|i| i["ItemName"] == "ICS425" && i["ItemType"] == "Channel")
        .unwrap()["ItemId"]
        .as_i64()
        .unwrap();

    // 1. GET operation mode, find the ICP mode by Description substring.
    let mut op_mode = get_json(&format!("{base}/item/operationMode/?itemId={channel_id}"));
    let icp_id = op_mode["Settings"][0]["SupportedValues"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["Description"].as_str().unwrap().contains("ICP"))
        .unwrap()["Id"]
        .clone();
    op_mode["Settings"][0]["Value"] = icp_id;
    put_json(
        &format!("{base}/item/operationMode/?itemId={channel_id}"),
        &op_mode,
    );

    // 2. Settings document now reflects ICP mode (Current Source appears) and
    //    is marked unapplied.
    let mut settings = get_json(&format!("{base}/item/settings/?itemId={channel_id}"));
    assert_eq!(settings["SettingsApplied"], false);
    assert_eq!(settings["OperationMode"]["Description"], "ICP® Input");
    let names: Vec<&str> = settings["Settings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["Name"].as_str().unwrap())
        .collect();
    assert!(names.contains(&"Current Source"), "got {names:?}");

    // 3. Mutate values + enable streaming via the Data array, PUT back.
    for setting in settings["Settings"].as_array_mut().unwrap() {
        if setting["Name"].as_str().unwrap().contains("Voltage Range") {
            setting["Value"] = 1.into();
        } else if setting["Name"].as_str().unwrap().contains("Coupling") {
            setting["Value"] = 0.into();
        }
    }
    for entry in settings["Data"].as_array_mut().unwrap() {
        if entry["Name"].as_str().unwrap().contains("Streaming") {
            entry["Value"] = 1.into();
        }
    }
    put_json(
        &format!("{base}/item/settings/?itemId={channel_id}"),
        &settings,
    );

    // 4. Apply; pending changes must report the restart status code (4).
    let apply: Value = ureq::put(&format!("{base}/system/settings/apply"))
        .call()
        .unwrap()
        .into_json()
        .unwrap();
    assert_eq!(apply["StatusCode"], 4);

    // 5. Everything applied; values stuck.
    let after = get_json(&format!("{base}/item/settings/?itemId={channel_id}"));
    assert_eq!(after["SettingsApplied"], true);
    let range = after["Settings"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["Name"] == "Voltage Range")
        .unwrap();
    assert_eq!(range["Value"], 1);

    // 6. Second apply with nothing pending: success, no restart.
    let apply_again: Value = ureq::put(&format!("{base}/system/settings/apply"))
        .call()
        .unwrap()
        .into_json()
        .unwrap();
    assert_eq!(apply_again["StatusCode"], 1);
}

#[test]
fn system_settings_tree() {
    let (_server, base) = start_sim();
    let tree = get_json(&format!("{base}/system/settings/"));
    assert_eq!(tree["ItemName"], "MicroQ");
    assert_eq!(tree["ItemType"], "Controller");
    // Controller -> SC -> modules; a MicroQ carries an SC10 (capture 2026-07-22).
    let sc = &tree["Children"][0];
    assert_eq!(sc["ItemName"], "SC10");
    let module_names: Vec<&str> = sc["Children"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| m["ItemName"].as_str().unwrap())
        .collect();
    assert_eq!(module_names, vec!["ICS425", "THM427"]);
    // THM427 has 8 channels.
    assert_eq!(sc["Children"][1]["Children"].as_array().unwrap().len(), 8);
}

#[test]
fn invalid_item_id_is_400_with_status_5() {
    let (_server, base) = start_sim();
    let err = ureq::get(&format!("{base}/item/settings/?itemId=999"))
        .call()
        .unwrap_err();
    match err {
        ureq::Error::Status(code, response) => {
            assert_eq!(code, 400);
            let body: Value = response.into_json().unwrap();
            assert_eq!(body["StatusCode"], 5);
            assert!(body.get("TypeCode").is_some());
            assert!(body.get("Message").is_some());
        }
        other => panic!("expected status error, got {other}"),
    }
}

#[test]
fn enum_value_out_of_range_is_rejected() {
    let (_server, base) = start_sim();
    let items = get_json(&format!("{base}/item/list/"));
    let channel_id = items
        .as_array()
        .unwrap()
        .iter()
        .find(|i| i["ItemName"] == "ICS425" && i["ItemType"] == "Channel")
        .unwrap()["ItemId"]
        .as_i64()
        .unwrap();

    let mut settings = get_json(&format!("{base}/item/settings/?itemId={channel_id}"));
    settings["Settings"][0]["Value"] = 99.into();
    let err = ureq::put(&format!("{base}/item/settings/?itemId={channel_id}"))
        .send_json(&settings)
        .unwrap_err();
    match err {
        ureq::Error::Status(code, response) => {
            assert_eq!(code, 400);
            let body: Value = response.into_json().unwrap();
            assert_eq!(body["StatusCode"], 2);
        }
        other => panic!("expected status error, got {other}"),
    }
}

#[test]
fn datastream_setup_shape() {
    let (server, base) = start_sim();
    let setup = get_json(&format!("{base}/dataStream/setup/"));
    assert_eq!(setup["TCPPort"], server.stream_port());
    assert_eq!(setup["WebSocketPort"], 8090);
    assert!(setup["IPAddresses"].as_array().unwrap().len() == 1);
}

/// The customer's MicroQ rack (capture 2026-07-22): built-in XMC237 under the
/// controller ahead of the SC10, role-suffixed channel names, the unexposed
/// ICP channel's ItemId gap at 3, TAC221 Scope channels nested under Tacho,
/// and Empty modules for vacant G2 slots.
#[test]
fn microq_item_list_matches_hardware_capture() {
    let config: SimConfig = toml::from_str(
        r#"
        [system]
        chassis = "MicroQ"
        serial = "SIM0001"
        master_sampling_rate = 131072

        [server]
        rest_port = 0
        stream_port = 0

        [[slots]]
        slot = 0
        module = "XMC237"
        builtin = true

        [[slots]]
        slot = 1
        module = "WSB42X2"

        [[slots]]
        slot = 2
        module = "TAC221"

        [[slots]]
        slot = 3
        module = "Empty"

        [[slots]]
        slot = 4
        module = "Empty"
        "#,
    )
    .unwrap();
    let server = SimServer::start(config).unwrap();
    let base = format!("http://127.0.0.1:{}", server.rest_port());

    let items = get_json(&format!("{base}/item/list/"));
    let listed: Vec<(i64, String, String)> = items
        .as_array()
        .unwrap()
        .iter()
        .map(|i| {
            (
                i["ItemId"].as_i64().unwrap(),
                i["ItemName"].as_str().unwrap().to_string(),
                i["ItemType"].as_str().unwrap().to_string(),
            )
        })
        .collect();
    let expected: Vec<(i64, String, String)> = [
        (1, "MicroQ", "Controller"),
        (2, "XMC237", "Module"),
        (4, "XMC237 GPS", "Channel"),
        (5, "XMC237 CAN FD", "Channel"),
        (6, "XMC237 CAN FD", "Channel"),
        (7, "SC10", "SignalConditioner"),
        (8, "WSB42X2", "Module"),
        (9, "WSB42X2", "Channel"),
        (10, "WSB42X2", "Channel"),
        (11, "WSB42X2", "Channel"),
        (12, "WSB42X2", "Channel"),
        (13, "TAC221", "Module"),
        (14, "TAC221 Tacho", "Channel"),
        (15, "TAC221 Scope", "Channel"),
        (16, "TAC221 Tacho", "Channel"),
        (17, "TAC221 Scope", "Channel"),
        (18, "Empty", "Module"),
        (19, "Empty", "Module"),
    ]
    .iter()
    .map(|(id, name, ty)| (*id, name.to_string(), ty.to_string()))
    .collect();
    assert_eq!(listed, expected);

    // Scope channels nest under their Tacho in the settings tree.
    let tree = get_json(&format!("{base}/system/settings/"));
    let tac = &tree["Children"][1]["Children"][1];
    assert_eq!(tac["ItemName"], "TAC221");
    let tacho = &tac["Children"][0];
    assert_eq!(tacho["ItemName"], "TAC221 Tacho");
    assert_eq!(tacho["Children"][0]["ItemName"], "TAC221 Scope");
}
