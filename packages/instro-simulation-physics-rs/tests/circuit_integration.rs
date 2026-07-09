//! Black-box `Circuit` usage matching the Python-facing story, without going through PyO3.

use instro_simulation_physics_rs::{Attachment, Circuit, InstrumentId, PsuMode};

#[test]
fn circuit_constructs_and_accepts_a_psu() {
    let mut circuit = Circuit::new();
    circuit.add_psu("psu1", 0.5);
    // No panics; nothing else asserted yet -- later steps add real behavior.
}

#[test]
fn resistive_load_settles_to_ohms_law_voltage_and_current() {
    let mut circuit = Circuit::new();
    circuit.add_psu("psu1", 0.5);
    circuit.set_psu_attachments(
        "psu1",
        vec![Attachment::Resistive {
            resistance_ohms: 1000.0,
            emf_volts: 0.0,
        }],
    );
    circuit.set_psu_voltage_setpoint("psu1", 10.0);
    circuit.set_psu_current_limit("psu1", 1.0);
    circuit.set_psu_output_enabled("psu1", true);

    // Settle the output capacitor's transient before checking the steady-state answer.
    circuit.step(0.01).unwrap();

    assert_eq!(circuit.psu_mode("psu1"), PsuMode::Cv);
    assert!((circuit.psu_voltage("psu1") - 10.0).abs() < 0.01);
    assert!((circuit.psu_current("psu1") - 10.0 / 1000.0).abs() < 1e-4);
}

#[test]
fn two_coupled_psus_share_one_bus_voltage_and_the_weaker_one_folds_back_to_cc() {
    // Cycle 1's proof the node graph is genuinely coupling-capable (Corrections 15/21):
    // coupling makes the two buses literally one shared node, so the outcome below is a
    // consequence of one shared KCL row, not a PSU-specific special case. The engine's
    // current limit is signed (`i <= limit`, matching today's Python `i_demand <=
    // i_limit`), so the fold-back happens on the *higher-setpoint* PSU: given the small
    // limit, "a" cannot source enough to lift the shared bus to 12 V and folds back to
    // CC, while "b" holds the bus near its own setpoint and sinks the crossing current.
    let mut circuit = Circuit::new();
    circuit.add_psu("a", 0.5);
    circuit.add_psu("b", 0.5);
    circuit.set_psu_voltage_setpoint("a", 12.0);
    circuit.set_psu_current_limit("a", 0.2);
    circuit.set_psu_output_enabled("a", true);
    circuit.set_psu_voltage_setpoint("b", 10.0);
    circuit.set_psu_current_limit("b", 5.0);
    circuit.set_psu_output_enabled("b", true);
    circuit.set_psu_attachments("a", vec![Attachment::Coupled(InstrumentId::new("b"))]);

    for _ in 0..100 {
        circuit.step(1e-4).unwrap();
    }

    // One shared voltage unknown: both PSUs read the identical bus voltage.
    let v_a = circuit.psu_voltage("a");
    let v_b = circuit.psu_voltage("b");
    assert!((v_a - v_b).abs() < 1e-9, "shared bus reads {v_a} vs {v_b}");
    // b holds CV at 10 V through its 0.5 ohm r_series while a sources its 0.2 A limit
    // into the shared node: v = 10 + 0.2 * 0.5.
    assert!((v_a - 10.1).abs() < 1e-3, "shared bus voltage {v_a}");
    assert_eq!(circuit.psu_mode("a"), PsuMode::Cc);
    assert_eq!(circuit.psu_mode("b"), PsuMode::Cv);
    // The crossing current is a derived consequence of conservation at the one shared
    // node: a sources its limit; b sinks exactly that current.
    assert!((circuit.psu_current("a") - 0.2).abs() < 1e-6);
    assert!((circuit.psu_current("b") - -0.2).abs() < 1e-3);
}

#[test]
fn uncoupling_restores_independent_regulation_from_the_shared_state() {
    let mut circuit = Circuit::new();
    circuit.add_psu("a", 0.5);
    circuit.add_psu("b", 0.5);
    circuit.set_psu_voltage_setpoint("a", 12.0);
    circuit.set_psu_current_limit("a", 0.2);
    circuit.set_psu_output_enabled("a", true);
    circuit.set_psu_voltage_setpoint("b", 10.0);
    circuit.set_psu_current_limit("b", 5.0);
    circuit.set_psu_output_enabled("b", true);
    circuit.set_psu_attachments("a", vec![Attachment::Coupled(InstrumentId::new("b"))]);
    for _ in 0..100 {
        circuit.step(1e-4).unwrap();
    }
    let shared_voltage = circuit.psu_voltage("a");

    // Shape change back to a synthetic load: a's bus becomes its own root again, with the
    // shared voltage it held at the instant of the cut as its initial state.
    circuit.set_psu_attachments(
        "a",
        vec![Attachment::Resistive {
            resistance_ohms: 1000.0,
            emf_volts: 0.0,
        }],
    );
    assert!((circuit.psu_voltage("a") - shared_voltage).abs() < 1e-9);

    for _ in 0..100 {
        circuit.step(1e-4).unwrap();
    }
    // a now regulates independently at its own setpoint (12 V minus the r_series drop)...
    let expected_current = 12.0 / 1000.5;
    assert_eq!(circuit.psu_mode("a"), PsuMode::Cv);
    assert!((circuit.psu_voltage("a") - (12.0 - expected_current * 0.5)).abs() < 1e-3);
    // ...and b, no longer sinking a's current, floats open at its own setpoint.
    assert_eq!(circuit.psu_mode("b"), PsuMode::Cv);
    assert!((circuit.psu_voltage("b") - 10.0).abs() < 1e-3);
}
