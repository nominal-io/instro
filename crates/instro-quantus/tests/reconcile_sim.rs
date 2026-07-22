//! End-to-end reconcile tests: the blocking client configuring a simulated
//! MicroQ rack shaped like the customer's (mics fast, thermocouples slow).

use instro_quantus::blocking::QuantusClient;
use instro_quantus::config::RackConfig;
use instro_quantus::error::Error;
use instro_quantus_sim::rest::SimServer;
use serde_json::Value;

fn start_sim() -> SimServer {
    let sim_config: instro_quantus_sim::config::SimConfig = toml::from_str(
        r#"
        [system]
        chassis = "MicroQ"
        serial = "SIM0001"
        master_sampling_rate = 204800

        [server]
        rest_port = 0
        stream_port = 0

        [[slots]]
        slot = 1
        module = "MIC42X7"

        [[slots]]
        slot = 2
        module = "THM427"

        [[slots]]
        slot = 3
        module = "ICS425"
        "#,
    )
    .unwrap();
    SimServer::start(sim_config).unwrap()
}

fn rack_config(port: u16) -> RackConfig {
    toml::from_str(&format!(
        r#"
        [device]
        name = "test_rig"

        [connection]
        host = "127.0.0.1"
        port = {port}

        [system]
        master_sampling_rate = 131072
        streaming_format = "Processed"

        [[modules]]
        name = "MIC42X7"
        sample_rate_hz = 65536.0

        [[modules.channels]]
        index = 1
        alias = "mic_inlet"
        mode = "Microphone Input"
        streaming = true
        settings = {{ "Voltage Range" = "1.2 V" }}

        [[modules]]
        name = "THM427"
        sample_rate_hz = 100.0

        [[modules.channels]]
        index = 1
        alias = "tc_exhaust_a"
        mode = "Thermocouple Type K Input"
        streaming = true

        [[modules.channels]]
        index = 2
        alias = "tc_exhaust_b"
        mode = "Thermocouple Type K Input"
        streaming = true

        [[modules]]
        name = "ICS425"

        [[modules.channels]]
        index = 3
        alias = "accel_z"
        mode = "ICP® Input"
        streaming = true
        settings = {{ "Voltage Range" = "1 V", "Coupling" = "AC with 1 Hz Filter" }}
        "#
    ))
    .unwrap()
}

fn setting_value(item: &instro_quantus_sim::model::ItemState, name: &str) -> Value {
    item.settings
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["Name"] == name)
        .unwrap_or_else(|| panic!("no setting '{name}'"))["Value"]
        .clone()
}

#[test]
fn full_reconcile_against_customer_shaped_rack() {
    let sim = start_sim();
    let client = QuantusClient::connect(rack_config(sim.rest_port())).unwrap();
    let report = client.reconcile().unwrap();

    // Reported achievements: pending settings mean the apply restarts the epoch.
    assert!(report.restart_required);
    assert_eq!(report.master_sampling_rate_hz, Some(131072.0));
    let mic = report.modules.iter().find(|m| m.name == "MIC42X7").unwrap();
    assert_eq!(mic.achieved_hz, Some(65536.0)); // exact: MSR/2
    assert_eq!(mic.divisor, Some(2.0));
    let thm = report.modules.iter().find(|m| m.name == "THM427").unwrap();
    // The customer's 100 Sa/s ask: snapped to the slowest achievable rate.
    assert_eq!(thm.achieved_hz, Some(512.0)); // MSR/256
    assert_eq!(report.channels.len(), 4);
    assert!(report.channels.iter().all(|c| c.item_id > 0));

    // Device-side state: inspect the sim directly.
    let state = sim.state.lock().unwrap();
    assert_eq!(state.epoch, 1);
    assert!(state.items.iter().all(|i| i.settings_applied));

    // Controller: MSR 131072 = Id 0, format Processed = Id 0.
    let controller = &state.items[0];
    assert_eq!(setting_value(controller, "Master Sampling Rate"), 0);
    assert_eq!(setting_value(controller, "Analog Data Streaming Format"), 0);

    // MIC42X7: divisor 2 = Id 1 in its family; channel 1 in Microphone mode (3)
    // with 1.2 V range (Id 1) and streaming on.
    let mic_module = state
        .items
        .iter()
        .find(|i| i.item_name == "MIC42X7" && i.item_type == "Module")
        .unwrap();
    assert_eq!(setting_value(mic_module, "Sample Rate"), 1);
    let mic_channel_idx = mic_module.children[0];
    let mic_channel = &state.items[mic_channel_idx];
    assert_eq!(mic_channel.current_mode, 3);
    assert_eq!(setting_value(mic_channel, "Voltage Range"), 1);
    let streaming = mic_channel.data.as_array().unwrap()[0]["Value"].clone();
    assert_eq!(streaming, 1);

    // THM427: divisor 256 = Id 5 in its family; pair setting switched channels
    // 1+2 into Type K mode (4) via module-level propagation.
    let thm_module = state
        .items
        .iter()
        .find(|i| i.item_name == "THM427" && i.item_type == "Module")
        .unwrap();
    assert_eq!(setting_value(thm_module, "Sample Rate"), 5);
    assert_eq!(
        setting_value(thm_module, "Channel 1 and 2 Operation Mode"),
        4
    );
    for offset in [0, 1] {
        let channel = &state.items[thm_module.children[offset]];
        assert_eq!(channel.current_mode, 4, "THM427 channel {offset} mode");
        assert_eq!(channel.data.as_array().unwrap()[0]["Value"], 1);
    }
    // Unconfigured pair untouched.
    let channel3 = &state.items[thm_module.children[2]];
    assert_eq!(channel3.current_mode, 1);

    // ICS425 channel 3: ICP mode (2), 1 V (Id 1), AC 1 Hz coupling (Id 1).
    let ics_module = state
        .items
        .iter()
        .find(|i| i.item_name == "ICS425" && i.item_type == "Module")
        .unwrap();
    let accel = &state.items[ics_module.children[2]];
    assert_eq!(accel.current_mode, 2);
    assert_eq!(setting_value(accel, "Voltage Range"), 1);
    assert_eq!(setting_value(accel, "Coupling"), 1);
}

#[test]
fn reconcile_recovers_disabled_modules_and_clears_persisted_streaming() {
    // A rack "left behind by a previous session": the module is Disabled and
    // two channels were left streaming (settings persist across power cycles).
    let sim_config: instro_quantus_sim::config::SimConfig = toml::from_str(
        r#"
        [system]
        chassis = "MicroQ"
        serial = "SIM0002"
        master_sampling_rate = 131072

        [server]
        rest_port = 0
        stream_port = 0

        [[slots]]
        slot = 1
        module = "ICS425"
        boot_mode = 0

        [[slots.channels]]
        index = 2
        boot_streaming = true

        [[slots.channels]]
        index = 4
        boot_streaming = true
        "#,
    )
    .unwrap();
    let sim = SimServer::start(sim_config).unwrap();

    let rack: RackConfig = toml::from_str(&format!(
        r#"
        [device]
        name = "test_rig"

        [connection]
        host = "127.0.0.1"
        port = {}

        [system]
        master_sampling_rate = 131072

        [[modules]]
        name = "ICS425"
        sample_rate_hz = 512.0

        [[modules.channels]]
        index = 1
        alias = "wanted"
        mode = "Voltage Input"
        streaming = true

        [[modules.channels]]
        index = 2
        alias = "explicitly_off"
        streaming = false
        "#,
        sim.rest_port()
    ))
    .unwrap();

    let client = QuantusClient::connect(rack).unwrap();
    let report = client.reconcile().unwrap();
    assert_eq!(report.channels.len(), 2);
    assert_eq!(report.channels[0].pulses_per_rev, 1.0);

    let state = sim.state.lock().unwrap();
    let module = state
        .items
        .iter()
        .find(|i| i.item_name == "ICS425" && i.item_type == "Module")
        .unwrap();
    // The Disabled module was recovered (declared content implies Enabled).
    assert_eq!(module.current_mode, 1);
    let streaming_value = |offset: usize| {
        state.items[module.children[offset]]
            .data
            .as_array()
            .unwrap()[0]["Value"]
            .clone()
    };
    assert_eq!(streaming_value(0), 1, "declared streaming=true -> Enabled");
    assert_eq!(
        streaming_value(1),
        0,
        "declared streaming=false -> Disabled"
    );
    assert_eq!(
        streaming_value(3),
        0,
        "undeclared leftover swept to Disabled"
    );
}

#[test]
fn reconcile_is_idempotent_on_second_run() {
    let sim = start_sim();
    let client = QuantusClient::connect(rack_config(sim.rest_port())).unwrap();
    let first = client.reconcile().unwrap();
    assert!(first.restart_required);
    // Second run rewrites the same values; settings go pending again, so the
    // sim (which restarts on any pending apply, assumption A4) reports another
    // restart — but state converges to the same values and everything applies.
    let second = client.reconcile().unwrap();
    assert_eq!(
        second
            .modules
            .iter()
            .map(|m| m.achieved_hz)
            .collect::<Vec<_>>(),
        first
            .modules
            .iter()
            .map(|m| m.achieved_hz)
            .collect::<Vec<_>>()
    );
    let state = sim.state.lock().unwrap();
    assert!(state.items.iter().all(|i| i.settings_applied));
}

#[test]
fn pair_mode_conflict_is_a_config_error() {
    let sim = start_sim();
    let mut config = rack_config(sim.rest_port());
    // Channels 1 and 2 share a THM427 pair; give them different TC types.
    config.modules[1].channels[1].mode = Some("Thermocouple Type J Input".into());
    let client = QuantusClient::connect(config).unwrap();
    match client.reconcile() {
        Err(Error::Config(message)) => {
            assert!(message.contains("pair"), "unexpected message: {message}")
        }
        other => panic!("expected config error, got {other:?}"),
    }
}

#[test]
fn unknown_module_is_a_config_error() {
    let sim = start_sim();
    let mut config = rack_config(sim.rest_port());
    config.modules[0].name = "CAN42S2".into();
    let client = QuantusClient::connect(config).unwrap();
    match client.reconcile() {
        Err(Error::Config(message)) => {
            assert!(message.contains("not found on device"), "{message}")
        }
        other => panic!("expected config error, got {other:?}"),
    }
}

#[test]
fn bad_enum_description_lists_options() {
    let sim = start_sim();
    let mut config = rack_config(sim.rest_port());
    config.modules[0].channels[0].settings.insert(
        "Voltage Range".into(),
        instro_quantus::config::SettingValue::Text("42 V".into()),
    );
    let client = QuantusClient::connect(config).unwrap();
    match client.reconcile() {
        Err(Error::Config(message)) => {
            assert!(message.contains("1.2 V"), "should list options: {message}")
        }
        other => panic!("expected config error, got {other:?}"),
    }
}

#[test]
fn data_stream_setup_is_reachable() {
    let sim = start_sim();
    let client = QuantusClient::connect(rack_config(sim.rest_port())).unwrap();
    let setup = client.data_stream_setup().unwrap();
    assert_eq!(setup["TCPPort"], sim.stream_port());
}

/// The customer's MicroQ shape end-to-end: built-in XMC237 discovered via
/// role-suffixed channel names, TAC221 reconciled without a module sample
/// rate (it has no Sample Rate setting), and a clear error when a config
/// requests one anyway.
#[test]
fn customer_microq_rack_reconciles() {
    let sim_config: instro_quantus_sim::config::SimConfig = toml::from_str(
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
        "#,
    )
    .unwrap();
    let sim = SimServer::start(sim_config).unwrap();

    let config: RackConfig = toml::from_str(&format!(
        r#"
        [device]
        name = "microq_rig"

        [connection]
        host = "127.0.0.1"
        port = {port}

        [[modules]]
        name = "WSB42X2"
        sample_rate_hz = 512.0

        [[modules.channels]]
        index = 1
        alias = "accel_z"
        mode = "ICP Input"
        streaming = true
        settings = {{ "Voltage Range" = "1 V", "Coupling" = "AC with 1 Hz Filter" }}

        [[modules]]
        name = "TAC221"

        [[modules.channels]]
        index = 1
        alias = "shaft"
        mode = "Enabled"
        streaming = true
        pulses_per_rev = 1.0

        [[modules]]
        name = "XMC237"

        [[modules.channels]]
        index = 2
        alias = "vehicle_bus"
        mode = "Listen Only"
        streaming = true
        "#,
        port = sim.rest_port()
    ))
    .unwrap();
    let client = QuantusClient::connect(config).unwrap();
    let report = client.reconcile().unwrap();

    let tac = report.modules.iter().find(|m| m.name == "TAC221").unwrap();
    assert_eq!(tac.requested_hz, None);
    assert_eq!(tac.achieved_hz, None);
    let by_alias: Vec<(&str, i64)> = report
        .channels
        .iter()
        .map(|c| (c.alias.as_str(), c.item_id))
        .collect();
    // ItemIds match the hardware capture: gap at 3, WSB ch1 = 9, Tacho#1 = 14,
    // CAN FD #1 = 5.
    assert_eq!(
        by_alias,
        vec![("accel_z", 9), ("shaft", 14), ("vehicle_bus", 5)]
    );

    // Requesting a rate on the rate-less TAC221 is a clear config error.
    let bad: RackConfig = toml::from_str(&format!(
        r#"
        [device]
        name = "microq_rig"

        [connection]
        host = "127.0.0.1"
        port = {port}

        [system]
        master_sampling_rate = 131072

        [[modules]]
        name = "TAC221"
        sample_rate_hz = 512.0
        "#,
        port = sim.rest_port()
    ))
    .unwrap();
    let error = QuantusClient::connect(bad)
        .unwrap()
        .reconcile()
        .unwrap_err();
    assert!(
        error.to_string().contains("no Sample Rate setting"),
        "unexpected error: {error}"
    );
}
