//! Node/component circuit-solving engine for instro's simulated instruments.
//!
//! Ports the node/component/solver architecture of `nominal-io/simulation` (`sim-engine`)
//! without depending on it: every registered instrument allocates nodes in one
//! `Circuit`-wide shared arena and stamps a small set of primitive components
//! (`Resistor`, `VoltageSource`, `CurrentSource`, `Capacitor`, `Ground`) whose
//! residual/Jacobian contributions are assembled and solved as one global backward-Euler
//! Newton system per step. Coupling two instruments (`Attachment::Coupled`) makes their
//! bus nodes literally the same node via a node-representative indirection. See the
//! session design doc at `.ailly/developer/2026-07-08-B-simulation-physics-engine/design.md`.

use std::collections::BTreeMap;

use nalgebra::{DMatrix, DVector};
use thiserror::Error;

/// Resistance floor/ceiling used in place of literal 0/∞ so a short or open circuit never
/// divides by zero -- the effective conductance saturates instead of blowing up, keeping
/// every residual/Jacobian entry finite. At realistic PSU voltage/current magnitudes the
/// resulting error is far below the tolerances `tests/psu/test_scpi_sim_server.py` already
/// asserts (sub-millivolt/sub-microamp), matching today's `math.isfinite`/`== 0` guards in
/// outcome (never NaN/inf) without branching on exact zero or infinity.
const MIN_RESISTANCE_OHMS: f64 = 1e-3;
const MAX_RESISTANCE_OHMS: f64 = 1e6;

fn effective_conductance(resistance_ohms: f64) -> f64 {
    if resistance_ohms <= 0.0 {
        1.0 / MIN_RESISTANCE_OHMS
    } else if !resistance_ohms.is_finite() {
        1.0 / MAX_RESISTANCE_OHMS
    } else {
        // f64::clamp is repo-disallowed (.clippy.toml: panics when min > max); MIN/MAX are
        // fixed constants with MIN < MAX, so max().min() is the same operation without the
        // disallowed method.
        #[allow(clippy::manual_clamp)]
        {
            1.0 / resistance_ohms
                .max(MIN_RESISTANCE_OHMS)
                .min(MAX_RESISTANCE_OHMS)
        }
    }
}

/// Distinct handle for a slot in `Circuit`'s single shared unknowns vector, allocated
/// exclusively by the crate-private `Circuit::add_node` (design Corrections 11/16).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NodeId(usize);

/// Distinct handle naming an instrument (PSU) within a `Circuit`.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct InstrumentId(String);

impl InstrumentId {
    pub fn new(id: &str) -> Self {
        InstrumentId(id.to_string())
    }
}

/// What a caller attaches to a PSU's `bus` (Correction 17: renamed from `Counterparty`,
/// held as a `Vec`). `Resistive` is a caller-facing preset owning real composed
/// components (Correction 23); `Coupled` is genuine node identity (Correction 21).
#[derive(Debug, Clone, PartialEq)]
pub enum Attachment {
    Resistive {
        resistance_ohms: f64,
        emf_volts: f64,
    },
    Coupled(InstrumentId),
}

/// Last-resolved fold-back state: which ideal source the PSU stamps this step.
///
/// `Unreg` is a genuine third discrete state, not a CV/CC sub-case: the output stage has
/// railed at its rated `voltage_max` and can satisfy neither the voltage setpoint nor the
/// current limit. It stamps a voltage source pinning `source_node` (the physical output
/// stage) at `voltage_max`, so the delivered voltage/current fall honestly short of the
/// setpoint instead of the internal terminal running away (the `.ailly` research doc's
/// "UNREG when the source cannot satisfy the setpoint").
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PsuMode {
    Cv,
    Cc,
    Unreg,
}

/// A source's value: constant this cycle; a future time-varying source adds a variant
/// here rather than a new component or attachment path (Correction 22).
#[derive(Debug, Clone, Copy, PartialEq)]
enum SourceValue {
    Constant(f64),
}

impl SourceValue {
    fn at(&self, _t: f64) -> f64 {
        match *self {
            SourceValue::Constant(value) => value,
        }
    }
}

/// Contract every stamped component implements, mirroring `sim-engine`'s `DAESystem`:
/// `residual` over `(t, x, x_dot)`; `jacobian` returns the `(∂f/∂x, ∂f/∂ẋ)` pair
/// backward Euler folds into `J_eff = J_x + (1/dt)·J_xdot` (Corrections 12/22). Every
/// component this cycle builds ignores `t`; only `Capacitor` has a nonzero `∂f/∂ẋ`.
trait Stamp {
    fn residual(&self, t: f64, x: &DVector<f64>, x_dot: &DVector<f64>, f: &mut DVector<f64>);
    fn jacobian(&self, t: f64, j_x: &mut DMatrix<f64>, j_xdot: &mut DMatrix<f64>);
}

/// KCL conductance stamp between `p` and `n`; no branch unknown (Correction 19).
#[derive(Debug, Clone, Copy)]
struct Resistor {
    p: NodeId,
    n: NodeId,
    resistance_ohms: f64,
}

/// Ideal voltage source: `v_p - v_n - value.at(t) = 0` over a branch unknown, which is
/// the current the source injects into `p`.
#[derive(Debug, Clone, Copy)]
struct VoltageSource {
    p: NodeId,
    n: NodeId,
    branch: NodeId,
    value: SourceValue,
}

/// Ideal current source; carries a branch unknown so a CV<->CC source swap is
/// layout-neutral (Correction 19a). The branch is pinned to `value` and injects at `p`
/// with the same orientation as `VoltageSource`, so `psu_current` reads one uniform sign
/// convention across a mode flip.
#[derive(Debug, Clone, Copy)]
struct CurrentSource {
    p: NodeId,
    n: NodeId,
    branch: NodeId,
    value: SourceValue,
}

/// Energy-storage element: the one nonzero `∂f/∂ẋ` block this cycle (Correction 12).
#[derive(Debug, Clone, Copy)]
struct Capacitor {
    p: NodeId,
    n: NodeId,
    capacitance_farads: f64,
}

/// Explicit grounding component (`v = 0`), one per instrument (Correction 13); its
/// branch unknown is the current it sinks, keeping the system square (Correction 19b).
#[derive(Debug, Clone, Copy)]
struct Ground {
    terminal: NodeId,
    branch: NodeId,
}

/// Pure algebraic constraint `v_dormant - v_root = 0` stamped for each non-root node a
/// `Coupled` repoint creates, keeping the full-size system square and the dormant slot's
/// entry valid for direct reads (Correction 21, realization (b)).
#[derive(Debug, Clone, Copy)]
struct MirrorRow {
    dormant: usize,
    root: usize,
}

/// Trivial `v = 0` row pinning a freed (currently unowned) arena slot so its row/column
/// stay non-singular until `add_node` reuses the slot (Correction 20's realloc path).
#[derive(Debug, Clone, Copy)]
struct FreeSlotRow {
    slot: usize,
}

impl Stamp for Resistor {
    fn residual(&self, _t: f64, x: &DVector<f64>, _x_dot: &DVector<f64>, f: &mut DVector<f64>) {
        let g = effective_conductance(self.resistance_ohms);
        let i = g * (x[self.p.0] - x[self.n.0]);
        f[self.p.0] -= i;
        f[self.n.0] += i;
    }

    fn jacobian(&self, _t: f64, j_x: &mut DMatrix<f64>, _j_xdot: &mut DMatrix<f64>) {
        let g = effective_conductance(self.resistance_ohms);
        j_x[(self.p.0, self.p.0)] -= g;
        j_x[(self.p.0, self.n.0)] += g;
        j_x[(self.n.0, self.p.0)] += g;
        j_x[(self.n.0, self.n.0)] -= g;
    }
}

impl Stamp for VoltageSource {
    fn residual(&self, t: f64, x: &DVector<f64>, _x_dot: &DVector<f64>, f: &mut DVector<f64>) {
        f[self.branch.0] += x[self.p.0] - x[self.n.0] - self.value.at(t);
        f[self.p.0] += x[self.branch.0];
        f[self.n.0] -= x[self.branch.0];
    }

    fn jacobian(&self, _t: f64, j_x: &mut DMatrix<f64>, _j_xdot: &mut DMatrix<f64>) {
        j_x[(self.branch.0, self.p.0)] += 1.0;
        j_x[(self.branch.0, self.n.0)] -= 1.0;
        j_x[(self.p.0, self.branch.0)] += 1.0;
        j_x[(self.n.0, self.branch.0)] -= 1.0;
    }
}

impl Stamp for CurrentSource {
    fn residual(&self, t: f64, x: &DVector<f64>, _x_dot: &DVector<f64>, f: &mut DVector<f64>) {
        f[self.branch.0] += x[self.branch.0] - self.value.at(t);
        f[self.p.0] += x[self.branch.0];
        f[self.n.0] -= x[self.branch.0];
    }

    fn jacobian(&self, _t: f64, j_x: &mut DMatrix<f64>, _j_xdot: &mut DMatrix<f64>) {
        j_x[(self.branch.0, self.branch.0)] += 1.0;
        j_x[(self.p.0, self.branch.0)] += 1.0;
        j_x[(self.n.0, self.branch.0)] -= 1.0;
    }
}

impl Stamp for Capacitor {
    fn residual(&self, _t: f64, _x: &DVector<f64>, x_dot: &DVector<f64>, f: &mut DVector<f64>) {
        let i = self.capacitance_farads * (x_dot[self.p.0] - x_dot[self.n.0]);
        f[self.p.0] -= i;
        f[self.n.0] += i;
    }

    fn jacobian(&self, _t: f64, _j_x: &mut DMatrix<f64>, j_xdot: &mut DMatrix<f64>) {
        let c = self.capacitance_farads;
        j_xdot[(self.p.0, self.p.0)] -= c;
        j_xdot[(self.p.0, self.n.0)] += c;
        j_xdot[(self.n.0, self.p.0)] += c;
        j_xdot[(self.n.0, self.n.0)] -= c;
    }
}

impl Stamp for Ground {
    fn residual(&self, _t: f64, x: &DVector<f64>, _x_dot: &DVector<f64>, f: &mut DVector<f64>) {
        f[self.branch.0] += x[self.terminal.0];
        f[self.terminal.0] += x[self.branch.0];
    }

    fn jacobian(&self, _t: f64, j_x: &mut DMatrix<f64>, _j_xdot: &mut DMatrix<f64>) {
        j_x[(self.branch.0, self.terminal.0)] += 1.0;
        j_x[(self.terminal.0, self.branch.0)] += 1.0;
    }
}

impl Stamp for MirrorRow {
    fn residual(&self, _t: f64, x: &DVector<f64>, _x_dot: &DVector<f64>, f: &mut DVector<f64>) {
        f[self.dormant] += x[self.dormant] - x[self.root];
    }

    fn jacobian(&self, _t: f64, j_x: &mut DMatrix<f64>, _j_xdot: &mut DMatrix<f64>) {
        j_x[(self.dormant, self.dormant)] += 1.0;
        j_x[(self.dormant, self.root)] -= 1.0;
    }
}

impl Stamp for FreeSlotRow {
    fn residual(&self, _t: f64, x: &DVector<f64>, _x_dot: &DVector<f64>, f: &mut DVector<f64>) {
        f[self.slot] += x[self.slot];
    }

    fn jacobian(&self, _t: f64, j_x: &mut DMatrix<f64>, _j_xdot: &mut DMatrix<f64>) {
        j_x[(self.slot, self.slot)] += 1.0;
    }
}

/// Allocation-free sum type over every stampable component so `Circuit::step` can
/// assemble one flat, terminal-resolved component list per solve.
#[derive(Debug, Clone, Copy)]
enum AnyComponent {
    Resistor(Resistor),
    Voltage(VoltageSource),
    Current(CurrentSource),
    Capacitor(Capacitor),
    Ground(Ground),
    Mirror(MirrorRow),
    FreeSlot(FreeSlotRow),
}

impl Stamp for AnyComponent {
    fn residual(&self, t: f64, x: &DVector<f64>, x_dot: &DVector<f64>, f: &mut DVector<f64>) {
        match self {
            AnyComponent::Resistor(c) => c.residual(t, x, x_dot, f),
            AnyComponent::Voltage(c) => c.residual(t, x, x_dot, f),
            AnyComponent::Current(c) => c.residual(t, x, x_dot, f),
            AnyComponent::Capacitor(c) => c.residual(t, x, x_dot, f),
            AnyComponent::Ground(c) => c.residual(t, x, x_dot, f),
            AnyComponent::Mirror(c) => c.residual(t, x, x_dot, f),
            AnyComponent::FreeSlot(c) => c.residual(t, x, x_dot, f),
        }
    }

    fn jacobian(&self, t: f64, j_x: &mut DMatrix<f64>, j_xdot: &mut DMatrix<f64>) {
        match self {
            AnyComponent::Resistor(c) => c.jacobian(t, j_x, j_xdot),
            AnyComponent::Voltage(c) => c.jacobian(t, j_x, j_xdot),
            AnyComponent::Current(c) => c.jacobian(t, j_x, j_xdot),
            AnyComponent::Capacitor(c) => c.jacobian(t, j_x, j_xdot),
            AnyComponent::Ground(c) => c.jacobian(t, j_x, j_xdot),
            AnyComponent::Mirror(c) => c.jacobian(t, j_x, j_xdot),
            AnyComponent::FreeSlot(c) => c.jacobian(t, j_x, j_xdot),
        }
    }
}

/// Newton-Raphson failure modes, ported from `sim-engine`'s `BackwardEuler` solver shape.
#[derive(Debug, Error, Clone, Copy, PartialEq)]
pub enum SolverError {
    #[error(
        "Newton-Raphson did not converge after {iterations} iterations (residual norm {residual_norm})"
    )]
    ConvergenceFailed {
        iterations: usize,
        residual_norm: f64,
    },
    #[error("Jacobian is singular; the network has no unique solution")]
    SingularJacobian,
}

const MAX_NEWTON_ITERATIONS: usize = 50;
const NEWTON_TOLERANCE: f64 = 1e-10;

/// Floor on the solver's discretization interval so a zero (or pathological) caller `dt`
/// never divides by zero; 1 ns of simulated time is far below anything the engine's own
/// time constants can respond to.
const MIN_DT_SECONDS: f64 = 1e-9;

/// One backward-Euler implicit solve, ported from `sim-engine`'s `BackwardEuler`:
/// `x_dot = (x - x_old)/dt`, `J_eff = J_x + (1/dt)·J_xdot`, Newton-iterate
/// `J_eff·Δx = -F` via LU decomposition until `‖F‖` or `‖Δx‖` drops below tolerance.
fn backward_euler_solve(
    components: &[&dyn Stamp],
    x_old: &DVector<f64>,
    dt_seconds: f64,
    t: f64,
) -> Result<DVector<f64>, SolverError> {
    let dt = dt_seconds.max(MIN_DT_SECONDS);
    let n = x_old.len();
    let mut x = x_old.clone();
    let mut residual_norm = f64::INFINITY;
    for _ in 0..MAX_NEWTON_ITERATIONS {
        let x_dot = (&x - x_old) / dt;
        let mut f = DVector::<f64>::zeros(n);
        for component in components {
            component.residual(t, &x, &x_dot, &mut f);
        }
        residual_norm = f.norm();
        if residual_norm < NEWTON_TOLERANCE {
            return Ok(x);
        }
        let mut j_x = DMatrix::<f64>::zeros(n, n);
        let mut j_xdot = DMatrix::<f64>::zeros(n, n);
        for component in components {
            component.jacobian(t, &mut j_x, &mut j_xdot);
        }
        let j_eff = j_x + j_xdot / dt;
        let delta = match j_eff.lu().solve(&(-&f)) {
            Some(delta) => delta,
            None => return Err(SolverError::SingularJacobian),
        };
        let delta_norm = delta.norm();
        x += delta;
        if delta_norm < NEWTON_TOLERANCE {
            return Ok(x);
        }
    }
    Err(SolverError::ConvergenceFailed {
        iterations: MAX_NEWTON_ITERATIONS,
        residual_norm,
    })
}

/// Default bulk output capacitance for a simulated PSU's output stage (Correction 8).
const DEFAULT_OUTPUT_CAPACITANCE_FARADS: f64 = 220e-6;

/// Number of equal implicit sub-solves `step` splits the caller's `dt_seconds` into.
///
/// Backward Euler is unconditionally stable at any `dt` but only first-order accurate: a
/// single implicit solve spanning a long idle interval (an SCPI caller can wait
/// arbitrarily long between queries) lands short of the settled state by a factor
/// `1/(1 + dt/τ)` -- ~2% for a 5 ms step against the output capacitor's ~110 µs τ, which
/// is outside the settling tolerances the simulator's regression tests assert. A fixed
/// split bounds that error at constant cost without reintroducing the first build's
/// stability-driven machinery: no stability bound, no adaptive substep count, no
/// cap-and-carry.
const STEP_SUBSTEPS: usize = 10;

/// Iteration bound for the per-sub-solve discrete CV/CC consistency loop. The pre-solve
/// mode guess is evaluated against the last solved state (design "Rust API" step 1) and
/// can be wrong across a large step -- a CC-guessed solve over a long idle `dt` would
/// charge the output capacitor far past the CV crossover within the one step -- so each
/// sub-solve re-checks the chosen modes against its own solved state and re-solves on a
/// flip. This terminates in one or two rounds away from the fold-back boundary; the cap
/// only guards marginal boundary chatter, where the last solve is accepted.
const MAX_MODE_ROUNDS: usize = 8;

/// The discrete, per-step choice of which ideal source a PSU stamps (design "Rust API"
/// step 1); both kinds own the same single branch slot, so a mode flip is layout-neutral
/// (Correction 19a).
#[derive(Debug, Clone, Copy)]
enum PsuSource {
    Voltage(VoltageSource),
    Current(CurrentSource),
}

/// Pairs the caller's requested `Attachment` with what resolving it owns
/// (Correction 23): `Resistive` owns a node+branch and real components, mutated in place
/// on a same-shape update (Correction 20); `Coupled` owns nothing -- resolving it is
/// purely the node-representative repoint (Correction 21).
#[derive(Debug, Clone)]
struct AttachmentState {
    config: Attachment,
    owned: AttachmentComponents,
}

#[derive(Debug, Clone)]
enum AttachmentComponents {
    Resistive {
        resistor: Resistor,
        source: VoltageSource,
    },
    Coupled,
}

/// A composite instrument: a small graph of the primitive components wired against
/// `Circuit`-allocated `NodeId`s (Correction 13).
#[derive(Debug, Clone)]
struct Psu {
    source_node: NodeId,
    bus: NodeId,
    gnd: NodeId,
    psu_branch: NodeId,
    r_series: Resistor,
    output_capacitance: Capacitor,
    ground: Ground,
    voltage_setpoint: f64,
    current_limit: f64,
    /// The output stage's rated compliance ceiling: `source_node` can never be driven
    /// above this, whatever the setpoint/limit/sense-mode demand. `f64::INFINITY` (the
    /// `add_psu` default) leaves the source unconstrained, so a caller that never sets a
    /// ceiling gets exactly the prior two-mode behavior.
    voltage_max: f64,
    output_enabled: bool,
    remote_sense: bool,
    attachments: Vec<AttachmentState>,
    mode: PsuMode,
}

impl Psu {
    /// Node the PSU's own ideal source regulates: `source_node` normally, or `bus`
    /// directly when remote-sensing (the feedback loop regulates at the sensed point,
    /// bypassing `r_series`'s drop).
    fn regulation_node(&self) -> NodeId {
        if self.remote_sense {
            self.bus
        } else {
            self.source_node
        }
    }

    /// This step's stamped source: a zero-amp current source when output-disabled
    /// (Correction 19c: layout-stable "output stage disconnected", no early return),
    /// otherwise the mode-selected ideal source.
    fn chosen_source(&self) -> PsuSource {
        let p = self.regulation_node();
        if !self.output_enabled {
            return PsuSource::Current(CurrentSource {
                p,
                n: self.gnd,
                branch: self.psu_branch,
                value: SourceValue::Constant(0.0),
            });
        }
        match self.mode {
            PsuMode::Cv => PsuSource::Voltage(VoltageSource {
                p,
                n: self.gnd,
                branch: self.psu_branch,
                value: SourceValue::Constant(self.voltage_setpoint),
            }),
            PsuMode::Cc => PsuSource::Current(CurrentSource {
                p,
                n: self.gnd,
                branch: self.psu_branch,
                value: SourceValue::Constant(self.current_limit),
            }),
            // Railed: the ceiling always binds at the physical output stage
            // (`source_node`), never at the sensed node -- remote sense compensates the
            // lead drop by raising `source_node`, and it is precisely that node the real
            // hardware cannot push past `voltage_max`.
            PsuMode::Unreg => PsuSource::Voltage(VoltageSource {
                p: self.source_node,
                n: self.gnd,
                branch: self.psu_branch,
                value: SourceValue::Constant(self.voltage_max),
            }),
        }
    }
}

/// A node/component circuit owning one shared arena, one unknowns vector, and one global
/// backward-Euler solve spanning every registered instrument (Corrections 11/14).
pub struct Circuit {
    n_unknowns: usize,
    psus: BTreeMap<InstrumentId, Psu>,
    x: DVector<f64>,
    /// Cumulative elapsed time, advanced by `dt_seconds` per `step` (Correction 22);
    /// every component this cycle builds ignores it.
    t: f64,
    /// Each node's representative: itself unless a `Coupled` repoint made it dormant
    /// (Correction 21). An indirection layer over stable indices -- no slot is remapped.
    node_representative: Vec<usize>,
    /// Arena slots released by an attachment shape change, reused before growing
    /// (Correction 20).
    free_slots: Vec<usize>,
}

impl Default for Circuit {
    fn default() -> Self {
        Self::new()
    }
}

impl Circuit {
    pub fn new() -> Self {
        Circuit {
            n_unknowns: 0,
            psus: BTreeMap::new(),
            x: DVector::zeros(0),
            t: 0.0,
            node_representative: Vec::new(),
            free_slots: Vec::new(),
        }
    }

    fn psu_mut(&mut self, id: &str) -> &mut Psu {
        self.psus
            .get_mut(&InstrumentId::new(id))
            .unwrap_or_else(|| panic!("no PSU registered with id {id:?}; call add_psu first"))
    }

    fn psu(&self, id: &str) -> &Psu {
        self.psus
            .get(&InstrumentId::new(id))
            .unwrap_or_else(|| panic!("no PSU registered with id {id:?}; call add_psu first"))
    }

    /// Allocates one slot in the shared unknowns vector, reusing a freed slot before
    /// growing (Correction 20). The sole way any `NodeId` is created (Correction 16).
    fn add_node(&mut self) -> NodeId {
        if let Some(slot) = self.free_slots.pop() {
            self.x[slot] = 0.0;
            self.node_representative[slot] = slot;
            return NodeId(slot);
        }
        let index = self.n_unknowns;
        self.n_unknowns += 1;
        self.node_representative.push(index);
        let x = std::mem::replace(&mut self.x, DVector::zeros(0));
        self.x = x.resize_vertically(self.n_unknowns, 0.0);
        NodeId(index)
    }

    /// Chases `node_representative` to the root. The hop bound is a defensive guard; the
    /// repoint discipline in `set_psu_attachments` never creates a cycle.
    fn resolve(&self, node: NodeId) -> NodeId {
        let mut current = node.0;
        for _ in 0..=self.node_representative.len() {
            let parent = self.node_representative[current];
            if parent == current {
                break;
            }
            current = parent;
        }
        NodeId(current)
    }

    pub fn add_psu(&mut self, id: &str, r_series: f64) {
        let source_node = self.add_node();
        let bus = self.add_node();
        let gnd = self.add_node();
        let gnd_branch = self.add_node();
        let psu_branch = self.add_node();
        let psu = Psu {
            source_node,
            bus,
            gnd,
            psu_branch,
            r_series: Resistor {
                p: source_node,
                n: bus,
                resistance_ohms: r_series,
            },
            output_capacitance: Capacitor {
                p: bus,
                n: gnd,
                capacitance_farads: DEFAULT_OUTPUT_CAPACITANCE_FARADS,
            },
            ground: Ground {
                terminal: gnd,
                branch: gnd_branch,
            },
            voltage_setpoint: 0.0,
            current_limit: 0.0,
            voltage_max: f64::INFINITY,
            output_enabled: false,
            remote_sense: false,
            attachments: Vec::new(),
            mode: PsuMode::Cv,
        };
        self.psus.insert(InstrumentId::new(id), psu);
    }

    /// Replaces the PSU's attachment list (Correction 17). A same-shape replacement
    /// updates owned component parameters in place and allocates nothing; a shape change
    /// frees and reallocates only attachment-scoped slots (Correction 20). `Coupled`
    /// entries own no slots; they only repoint this PSU's `bus` (Correction 21).
    pub fn set_psu_attachments(&mut self, id: &str, attachments: Vec<Attachment>) {
        let key = InstrumentId::new(id);
        let mut psu = self
            .psus
            .remove(&key)
            .unwrap_or_else(|| panic!("no PSU registered with id {id:?}; call add_psu first"));

        let same_shape = psu.attachments.len() == attachments.len()
            && psu
                .attachments
                .iter()
                .zip(&attachments)
                .all(|(state, config)| {
                    matches!(
                        (&state.config, config),
                        (Attachment::Resistive { .. }, Attachment::Resistive { .. })
                            | (Attachment::Coupled(_), Attachment::Coupled(_))
                    )
                });

        if same_shape {
            for (state, config) in psu.attachments.iter_mut().zip(&attachments) {
                if let (
                    AttachmentComponents::Resistive { resistor, source },
                    Attachment::Resistive {
                        resistance_ohms,
                        emf_volts,
                    },
                ) = (&mut state.owned, config)
                {
                    resistor.resistance_ohms = *resistance_ohms;
                    source.value = SourceValue::Constant(*emf_volts);
                }
                state.config = config.clone();
            }
        } else {
            for state in psu.attachments.drain(..) {
                if let AttachmentComponents::Resistive { resistor, source } = state.owned {
                    for slot in [resistor.n.0, source.branch.0] {
                        self.x[slot] = 0.0;
                        self.node_representative[slot] = slot;
                        self.free_slots.push(slot);
                    }
                }
            }
            for config in &attachments {
                let owned = match config {
                    Attachment::Resistive {
                        resistance_ohms,
                        emf_volts,
                    } => {
                        let counterparty_node = self.add_node();
                        let emf_branch = self.add_node();
                        AttachmentComponents::Resistive {
                            resistor: Resistor {
                                p: psu.bus,
                                n: counterparty_node,
                                resistance_ohms: *resistance_ohms,
                            },
                            source: VoltageSource {
                                p: counterparty_node,
                                n: psu.gnd,
                                branch: emf_branch,
                                value: SourceValue::Constant(*emf_volts),
                            },
                        }
                    }
                    Attachment::Coupled(_) => AttachmentComponents::Coupled,
                };
                psu.attachments.push(AttachmentState {
                    config: config.clone(),
                    owned,
                });
            }
        }

        self.recompute_bus_representative(&mut psu);
        self.psus.insert(key, psu);
    }

    /// Recomputes this PSU's `bus` representative from its attachment list: identity
    /// unless a `Coupled` entry points it at the partner's root (Correction 21). The
    /// self-root guard makes a reciprocal `Coupled` a no-op rather than a cycle.
    fn recompute_bus_representative(&mut self, psu: &mut Psu) {
        self.node_representative[psu.bus.0] = psu.bus.0;
        for state in &psu.attachments {
            if let Attachment::Coupled(other) = &state.config {
                let other_bus = self
                    .psus
                    .get(other)
                    .unwrap_or_else(|| {
                        panic!("no PSU registered with id {:?} to couple to", other.0)
                    })
                    .bus;
                let root = self.resolve(other_bus);
                if root != psu.bus {
                    self.node_representative[psu.bus.0] = root.0;
                }
            }
        }
    }

    pub fn set_psu_mode(&mut self, id: &str, mode: PsuMode) {
        self.psu_mut(id).mode = mode;
    }

    pub fn set_psu_voltage_setpoint(&mut self, id: &str, volts: f64) {
        self.psu_mut(id).voltage_setpoint = volts;
    }

    pub fn set_psu_current_limit(&mut self, id: &str, amps: f64) {
        self.psu_mut(id).current_limit = amps;
    }

    /// Sets the output stage's rated compliance ceiling (`source_node` never exceeds it).
    pub fn set_psu_voltage_max(&mut self, id: &str, volts: f64) {
        self.psu_mut(id).voltage_max = volts;
    }

    /// Updates the force-lead (probe) resistance between `source_node` and `bus`. A bare
    /// scalar on an already-stamped `Resistor` -- no node/branch reallocation -- so it is
    /// safe to call every step, keeping the engine's lead model in step with a caller that
    /// changes the probe resistance after registration.
    pub fn set_psu_r_series(&mut self, id: &str, ohms: f64) {
        self.psu_mut(id).r_series.resistance_ohms = ohms;
    }

    pub fn set_psu_output_enabled(&mut self, id: &str, enabled: bool) {
        self.psu_mut(id).output_enabled = enabled;
    }

    pub fn set_psu_remote_sense(&mut self, id: &str, enabled: bool) {
        self.psu_mut(id).remote_sense = enabled;
    }

    /// The current the CV candidate would source, evaluated against a solved state
    /// (design "Rust API" step 1): `(setpoint - v_bus)/r_series` on the normal path; the
    /// attachments' draw at the setpoint (plus the derived coupled crossing current)
    /// under remote sense, where the source pins `bus` directly.
    fn cv_candidate_current(&self, psu: &Psu, x: &DVector<f64>) -> f64 {
        let v_bus = x[self.resolve(psu.bus).0];
        if !psu.remote_sense {
            return (psu.voltage_setpoint - v_bus)
                * effective_conductance(psu.r_series.resistance_ohms);
        }
        let mut candidate = 0.0;
        let mut resistive_draw = 0.0;
        let mut has_coupled = false;
        for state in &psu.attachments {
            match &state.config {
                Attachment::Resistive {
                    resistance_ohms,
                    emf_volts,
                } => {
                    let g = effective_conductance(*resistance_ohms);
                    candidate += (psu.voltage_setpoint - emf_volts) * g;
                    resistive_draw += (v_bus - emf_volts) * g;
                }
                Attachment::Coupled(_) => has_coupled = true,
            }
        }
        if has_coupled {
            candidate += x[psu.psu_branch.0] - resistive_draw;
        }
        candidate
    }

    /// Assembles the flat, terminal-resolved component list for one solve: every PSU's
    /// own components and chosen source, every attachment's owned components (uniformly,
    /// no variant dispatch -- Correction 23), one mirror row per dormant node
    /// (Correction 21), and one pin row per freed slot (Correction 20).
    fn assemble(&self) -> Vec<AnyComponent> {
        let mut components = Vec::new();
        for psu in self.psus.values() {
            let r = psu.r_series;
            components.push(AnyComponent::Resistor(Resistor {
                p: self.resolve(r.p),
                n: self.resolve(r.n),
                ..r
            }));
            let c = psu.output_capacitance;
            components.push(AnyComponent::Capacitor(Capacitor {
                p: self.resolve(c.p),
                n: self.resolve(c.n),
                ..c
            }));
            let g = psu.ground;
            components.push(AnyComponent::Ground(Ground {
                terminal: self.resolve(g.terminal),
                ..g
            }));
            match psu.chosen_source() {
                PsuSource::Voltage(source) => {
                    components.push(AnyComponent::Voltage(VoltageSource {
                        p: self.resolve(source.p),
                        n: self.resolve(source.n),
                        ..source
                    }))
                }
                PsuSource::Current(source) => {
                    components.push(AnyComponent::Current(CurrentSource {
                        p: self.resolve(source.p),
                        n: self.resolve(source.n),
                        ..source
                    }))
                }
            }
            for state in &psu.attachments {
                match &state.owned {
                    AttachmentComponents::Resistive { resistor, source } => {
                        components.push(AnyComponent::Resistor(Resistor {
                            p: self.resolve(resistor.p),
                            n: self.resolve(resistor.n),
                            ..*resistor
                        }));
                        components.push(AnyComponent::Voltage(VoltageSource {
                            p: self.resolve(source.p),
                            n: self.resolve(source.n),
                            ..*source
                        }));
                    }
                    AttachmentComponents::Coupled => {}
                }
            }
        }
        for index in 0..self.n_unknowns {
            if self.node_representative[index] != index {
                components.push(AnyComponent::Mirror(MirrorRow {
                    dormant: index,
                    root: self.resolve(NodeId(index)).0,
                }));
            }
        }
        for &slot in &self.free_slots {
            components.push(AnyComponent::FreeSlot(FreeSlotRow { slot }));
        }
        components
    }

    fn solve_once(&self, dt: f64) -> Result<DVector<f64>, SolverError> {
        let components = self.assemble();
        let refs: Vec<&dyn Stamp> = components
            .iter()
            .map(|component| component as &dyn Stamp)
            .collect();
        backward_euler_solve(&refs, &self.x, dt, self.t)
    }

    /// Modes whose stamped solve came out inconsistent with themselves, given the rated
    /// compliance ceiling on `source_node`: a CV source over its current limit or past the
    /// rail, a CC source past the rail or whose CV candidate now fits, or a railed (UNREG)
    /// source the rail no longer constrains. A single flip + re-solve + re-check lands on
    /// the binding constraint within `MAX_MODE_ROUNDS`; exactly one mode is self-consistent
    /// away from a boundary, so the checks below cannot ping-pong.
    fn mode_flips(&self, x: &DVector<f64>) -> Vec<(InstrumentId, PsuMode)> {
        let mut flips = Vec::new();
        for (id, psu) in &self.psus {
            if !psu.output_enabled {
                continue;
            }
            let i = x[psu.psu_branch.0];
            // Physical output-stage voltage. The load current always returns through the
            // force leads (`r_series`) in reality, so the output stage sits `i·r_series`
            // above the bus -- whichever node the ideal source was stamped at this step.
            // In local sense this equals x[source_node] (the source is at source_node); in
            // remote sense the source is relocated to `bus`, leaving x[source_node] a
            // floating dangling node, so the drop must be reconstructed here instead.
            let v_output = x[self.resolve(psu.bus).0] + i * psu.r_series.resistance_ohms;
            let v_reg = x[self.resolve(psu.regulation_node()).0];
            match psu.mode {
                PsuMode::Cv => {
                    if i > psu.current_limit {
                        flips.push((id.clone(), PsuMode::Cc));
                    } else if v_output > psu.voltage_max {
                        flips.push((id.clone(), PsuMode::Unreg));
                    }
                }
                PsuMode::Cc => {
                    if v_output > psu.voltage_max {
                        flips.push((id.clone(), PsuMode::Unreg));
                    } else if self.cv_candidate_current(psu, x) <= psu.current_limit {
                        flips.push((id.clone(), PsuMode::Cv));
                    }
                }
                PsuMode::Unreg => {
                    // At the rail: if delivered current still overruns the limit, CC binds
                    // lower (pulling source_node back under the ceiling); if the rail alone
                    // already reaches the regulated setpoint, CV binds. Otherwise the rail
                    // is the genuine operating point -- the setpoint is unsatisfiable.
                    if i > psu.current_limit {
                        flips.push((id.clone(), PsuMode::Cc));
                    } else if v_reg >= psu.voltage_setpoint {
                        flips.push((id.clone(), PsuMode::Cv));
                    }
                }
            }
        }
        flips
    }

    fn substep(&mut self, dt: f64) -> Result<(), SolverError> {
        let guesses: Vec<(InstrumentId, PsuMode)> = self
            .psus
            .iter()
            .filter(|(_, psu)| psu.output_enabled)
            .map(|(id, psu)| {
                let mode = if self.cv_candidate_current(psu, &self.x) <= psu.current_limit {
                    PsuMode::Cv
                } else {
                    PsuMode::Cc
                };
                (id.clone(), mode)
            })
            .collect();
        for (id, mode) in guesses {
            if let Some(psu) = self.psus.get_mut(&id) {
                psu.mode = mode;
            }
        }

        let mut x_new = self.solve_once(dt)?;
        for _ in 0..MAX_MODE_ROUNDS {
            let flips = self.mode_flips(&x_new);
            if flips.is_empty() {
                break;
            }
            for (id, mode) in flips {
                if let Some(psu) = self.psus.get_mut(&id) {
                    psu.mode = mode;
                }
            }
            x_new = self.solve_once(dt)?;
        }
        self.x = x_new;
        Ok(())
    }

    /// Advances the whole shared graph by `dt_seconds` as `STEP_SUBSTEPS` equal implicit
    /// backward-Euler solves, each spanning every registered instrument (Correction 14).
    pub fn step(&mut self, dt_seconds: f64) -> Result<(), SolverError> {
        let dt_slice = dt_seconds / STEP_SUBSTEPS as f64;
        for _ in 0..STEP_SUBSTEPS {
            self.t += dt_slice;
            self.substep(dt_slice)?;
        }
        Ok(())
    }

    pub fn psu_voltage(&self, id: &str) -> f64 {
        let psu = self.psu(id);
        self.x[self.resolve(psu.bus).0]
    }

    pub fn psu_current(&self, id: &str) -> f64 {
        let psu = self.psu(id);
        self.x[psu.psu_branch.0]
    }

    pub fn psu_mode(&self, id: &str) -> PsuMode {
        self.psu(id).mode
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn new_enabled_psu(circuit: &mut Circuit, id: &str, r_series: f64) {
        circuit.add_psu(id, r_series);
        circuit.set_psu_output_enabled(id, true);
    }

    fn attach_resistive(circuit: &mut Circuit, id: &str, resistance_ohms: f64, emf_volts: f64) {
        circuit.set_psu_attachments(
            id,
            vec![Attachment::Resistive {
                resistance_ohms,
                emf_volts,
            }],
        );
    }

    // ---- Component stamps (design "Testing strategy": hand-derived values) ----

    #[test]
    fn resistor_residual_and_jacobian_match_hand_derived_values() {
        let resistor = Resistor {
            p: NodeId(0),
            n: NodeId(1),
            resistance_ohms: 2.0,
        };
        let x = DVector::from_vec(vec![10.0, 4.0]);
        let x_dot = DVector::zeros(2);
        let mut f = DVector::zeros(2);
        resistor.residual(0.0, &x, &x_dot, &mut f);
        // I = (10 - 4) / 2 = 3: leaves node 0, enters node 1.
        assert!((f[0] - -3.0).abs() < 1e-12);
        assert!((f[1] - 3.0).abs() < 1e-12);

        let mut j_x = DMatrix::zeros(2, 2);
        let mut j_xdot = DMatrix::zeros(2, 2);
        resistor.jacobian(0.0, &mut j_x, &mut j_xdot);
        assert!((j_x[(0, 0)] - -0.5).abs() < 1e-12);
        assert!((j_x[(0, 1)] - 0.5).abs() < 1e-12);
        assert!((j_x[(1, 0)] - 0.5).abs() < 1e-12);
        assert!((j_x[(1, 1)] - -0.5).abs() < 1e-12);
        assert_eq!(j_xdot, DMatrix::zeros(2, 2));
    }

    #[test]
    fn capacitor_xdot_term_is_the_only_nonzero_dfdxdot_block() {
        let capacitance_farads = 220e-6;
        let cap = Capacitor {
            p: NodeId(0),
            n: NodeId(1),
            capacitance_farads,
        };
        let x = DVector::zeros(2);
        let x_dot = DVector::from_vec(vec![3.0, 1.0]);
        let mut f = DVector::zeros(2);
        cap.residual(0.0, &x, &x_dot, &mut f);
        // i = C·(v̇_p - v̇_n) = C·2 flows from p to n: leaves node 0, enters node 1
        // (same orientation as the Resistor's KCL stamp).
        let i = capacitance_farads * 2.0;
        assert!((f[0] - -i).abs() < 1e-15);
        assert!((f[1] - i).abs() < 1e-15);

        let mut j_x = DMatrix::zeros(2, 2);
        let mut j_xdot = DMatrix::zeros(2, 2);
        cap.jacobian(0.0, &mut j_x, &mut j_xdot);
        assert_eq!(j_x, DMatrix::zeros(2, 2));
        let c = capacitance_farads;
        assert!((j_xdot[(0, 0)] - -c).abs() < 1e-15);
        assert!((j_xdot[(0, 1)] - c).abs() < 1e-15);
        assert!((j_xdot[(1, 0)] - c).abs() < 1e-15);
        assert!((j_xdot[(1, 1)] - -c).abs() < 1e-15);
    }

    #[test]
    fn voltage_source_residual_and_jacobian_match_hand_derived_values() {
        let source = VoltageSource {
            p: NodeId(0),
            n: NodeId(1),
            branch: NodeId(2),
            value: SourceValue::Constant(5.0),
        };
        let x = DVector::from_vec(vec![7.0, 1.0, 1.5]);
        let x_dot = DVector::zeros(3);
        let mut f = DVector::zeros(3);
        source.residual(0.0, &x, &x_dot, &mut f);
        // Constraint row: v_p - v_n - V = 7 - 1 - 5 = 1; branch current 1.5 enters p,
        // returns at n.
        assert!((f[2] - 1.0).abs() < 1e-12);
        assert!((f[0] - 1.5).abs() < 1e-12);
        assert!((f[1] - -1.5).abs() < 1e-12);

        let mut j_x = DMatrix::zeros(3, 3);
        let mut j_xdot = DMatrix::zeros(3, 3);
        source.jacobian(0.0, &mut j_x, &mut j_xdot);
        assert!((j_x[(2, 0)] - 1.0).abs() < 1e-12);
        assert!((j_x[(2, 1)] - -1.0).abs() < 1e-12);
        assert!((j_x[(0, 2)] - 1.0).abs() < 1e-12);
        assert!((j_x[(1, 2)] - -1.0).abs() < 1e-12);
        assert_eq!(j_xdot, DMatrix::zeros(3, 3));
    }

    #[test]
    fn current_source_pins_its_branch_to_the_sourced_current() {
        let source = CurrentSource {
            p: NodeId(0),
            n: NodeId(1),
            branch: NodeId(2),
            value: SourceValue::Constant(2.0),
        };
        let x = DVector::from_vec(vec![0.0, 0.0, 1.5]);
        let x_dot = DVector::zeros(3);
        let mut f = DVector::zeros(3);
        source.residual(0.0, &x, &x_dot, &mut f);
        // Constraint row: i - I = 1.5 - 2 = -0.5; the branch current enters p and returns
        // at n with the same orientation as VoltageSource, so `psu_current` reads one
        // uniform sign convention across a CV<->CC flip.
        assert!((f[2] - -0.5).abs() < 1e-12);
        assert!((f[0] - 1.5).abs() < 1e-12);
        assert!((f[1] - -1.5).abs() < 1e-12);

        let mut j_x = DMatrix::zeros(3, 3);
        let mut j_xdot = DMatrix::zeros(3, 3);
        source.jacobian(0.0, &mut j_x, &mut j_xdot);
        assert!((j_x[(2, 2)] - 1.0).abs() < 1e-12);
        assert!((j_x[(0, 2)] - 1.0).abs() < 1e-12);
        assert!((j_x[(1, 2)] - -1.0).abs() < 1e-12);
        assert_eq!(j_xdot, DMatrix::zeros(3, 3));
    }

    #[test]
    fn ground_residual_and_jacobian_match_hand_derived_values() {
        let ground = Ground {
            terminal: NodeId(0),
            branch: NodeId(1),
        };
        let x = DVector::from_vec(vec![4.0, 2.0]);
        let x_dot = DVector::zeros(2);
        let mut f = DVector::zeros(2);
        ground.residual(0.0, &x, &x_dot, &mut f);
        // Constraint row: v = 4; the sunk current 2 balances the terminal's KCL row.
        assert!((f[1] - 4.0).abs() < 1e-12);
        assert!((f[0] - 2.0).abs() < 1e-12);

        let mut j_x = DMatrix::zeros(2, 2);
        let mut j_xdot = DMatrix::zeros(2, 2);
        ground.jacobian(0.0, &mut j_x, &mut j_xdot);
        assert!((j_x[(1, 0)] - 1.0).abs() < 1e-12);
        assert!((j_x[(0, 1)] - 1.0).abs() < 1e-12);
        assert_eq!(j_xdot, DMatrix::zeros(2, 2));
    }

    #[test]
    fn source_value_constant_residual_is_identical_at_two_different_t() {
        let source = VoltageSource {
            p: NodeId(0),
            n: NodeId(1),
            branch: NodeId(2),
            value: SourceValue::Constant(5.0),
        };
        let x = DVector::from_vec(vec![7.0, 1.0, 1.5]);
        let x_dot = DVector::zeros(3);
        let mut f_at_zero = DVector::zeros(3);
        source.residual(0.0, &x, &x_dot, &mut f_at_zero);
        let mut f_at_later = DVector::zeros(3);
        source.residual(1.7, &x, &x_dot, &mut f_at_later);
        assert_eq!(f_at_zero, f_at_later);
    }

    // ---- Solver (design "Testing strategy": solver-specific unit tests) ----

    #[test]
    fn linear_network_residual_is_below_tolerance_after_one_newton_update() {
        // source(10V) -> r_series(0.5) -> r_load(1000) -> gnd; 5 unknowns:
        // node 0 (source), node 1 (bus), node 2 (gnd), source branch 3, gnd branch 4.
        let source = VoltageSource {
            p: NodeId(0),
            n: NodeId(2),
            branch: NodeId(3),
            value: SourceValue::Constant(10.0),
        };
        let r_series = Resistor {
            p: NodeId(0),
            n: NodeId(1),
            resistance_ohms: 0.5,
        };
        let r_load = Resistor {
            p: NodeId(1),
            n: NodeId(2),
            resistance_ohms: 1000.0,
        };
        let ground = Ground {
            terminal: NodeId(2),
            branch: NodeId(4),
        };
        let components: Vec<&dyn Stamp> = vec![&source, &r_series, &r_load, &ground];

        // One manual Newton update from x = 0: for a linear network the first LU solve
        // lands exactly on the root, so the residual there is already ~0.
        let x = DVector::<f64>::zeros(5);
        let x_dot = DVector::<f64>::zeros(5);
        let mut f = DVector::<f64>::zeros(5);
        let mut j_x = DMatrix::<f64>::zeros(5, 5);
        let mut j_xdot = DMatrix::<f64>::zeros(5, 5);
        for component in &components {
            component.residual(0.0, &x, &x_dot, &mut f);
            component.jacobian(0.0, &mut j_x, &mut j_xdot);
        }
        let delta = j_x.lu().solve(&(-&f)).unwrap();
        let x_updated = &x + delta;

        let mut f_updated = DVector::<f64>::zeros(5);
        for component in &components {
            component.residual(0.0, &x_updated, &x_dot, &mut f_updated);
        }
        assert!(f_updated.norm() < 1e-9);
        // The updated point is the closed-form Ohm's-law answer.
        assert!((x_updated[0] - 10.0).abs() < 1e-9);
        assert!((x_updated[1] - 10.0 * 1000.0 / 1000.5).abs() < 1e-9);
    }

    #[test]
    fn backward_euler_solve_converges_on_a_linear_rc_network() {
        let source = VoltageSource {
            p: NodeId(0),
            n: NodeId(2),
            branch: NodeId(3),
            value: SourceValue::Constant(10.0),
        };
        let r_series = Resistor {
            p: NodeId(0),
            n: NodeId(1),
            resistance_ohms: 0.5,
        };
        let cap = Capacitor {
            p: NodeId(1),
            n: NodeId(2),
            capacitance_farads: 220e-6,
        };
        let ground = Ground {
            terminal: NodeId(2),
            branch: NodeId(4),
        };
        let components: Vec<&dyn Stamp> = vec![&source, &r_series, &cap, &ground];
        let x_old = DVector::<f64>::zeros(5);

        // One backward-Euler step of the RC charge: v1 = v_f·(dt/τ)/(1 + dt/τ).
        let dt = 1e-4;
        let tau = 0.5 * 220e-6;
        let x = backward_euler_solve(&components, &x_old, dt, 0.0).unwrap();
        let expected = 10.0 * (dt / tau) / (1.0 + dt / tau);
        assert!(
            (x[1] - expected).abs() < 1e-9,
            "v_bus {} vs analytic {expected}",
            x[1]
        );
    }

    #[test]
    fn conflicting_voltage_sources_on_the_same_node_pair_are_singular() {
        let source_a = VoltageSource {
            p: NodeId(0),
            n: NodeId(1),
            branch: NodeId(2),
            value: SourceValue::Constant(5.0),
        };
        let source_b = VoltageSource {
            p: NodeId(0),
            n: NodeId(1),
            branch: NodeId(3),
            value: SourceValue::Constant(7.0),
        };
        let ground = Ground {
            terminal: NodeId(1),
            branch: NodeId(4),
        };
        let components: Vec<&dyn Stamp> = vec![&source_a, &source_b, &ground];
        let result = backward_euler_solve(&components, &DVector::zeros(5), 1e-3, 0.0);
        assert_eq!(result, Err(SolverError::SingularJacobian));
    }

    #[test]
    fn zero_dt_solve_does_not_divide_by_zero_or_produce_nan() {
        let source = VoltageSource {
            p: NodeId(0),
            n: NodeId(2),
            branch: NodeId(3),
            value: SourceValue::Constant(10.0),
        };
        let r_series = Resistor {
            p: NodeId(0),
            n: NodeId(1),
            resistance_ohms: 0.5,
        };
        let cap = Capacitor {
            p: NodeId(1),
            n: NodeId(2),
            capacitance_farads: 220e-6,
        };
        let ground = Ground {
            terminal: NodeId(2),
            branch: NodeId(4),
        };
        let components: Vec<&dyn Stamp> = vec![&source, &r_series, &cap, &ground];
        let x = backward_euler_solve(&components, &DVector::zeros(5), 0.0, 0.0).unwrap();
        assert!(x.iter().all(|value| value.is_finite()));
        // Effectively no elapsed time: the capacitor holds its (zero) initial voltage.
        assert!(x[1].abs() < 1e-3);
    }

    // ---- Circuit-level regulation (design "Testing strategy": fold-back and edges) ----

    #[test]
    fn add_psu_does_not_panic() {
        let mut circuit = Circuit::new();
        circuit.add_psu("psu1", 0.5);
    }

    #[test]
    fn resistive_cv_fold_back_matches_closed_form_ohms_law() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.5);
        attach_resistive(&mut circuit, "psu1", 1000.0, 0.0);
        circuit.set_psu_voltage_setpoint("psu1", 10.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        // Settle the output capacitor's transient (many multiples of tau) before
        // checking the steady-state, closed-form answer.
        circuit.step(0.01).unwrap();

        // Closed-form Ohm's law: I = V / (r_series + r_load), V_bus = V - I * r_series.
        let expected_current = 10.0 / 1000.5;
        let expected_voltage = 10.0 - expected_current * 0.5;
        assert!((circuit.psu_current("psu1") - expected_current).abs() < 1e-4);
        assert!((circuit.psu_voltage("psu1") - expected_voltage).abs() < 1e-4);
        assert_eq!(circuit.psu_mode("psu1"), PsuMode::Cv);
    }

    #[test]
    fn current_limit_below_cv_candidate_folds_back_to_cc() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.5);
        attach_resistive(&mut circuit, "psu1", 0.1, 0.0);
        circuit.set_psu_voltage_setpoint("psu1", 5.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        circuit.step(0.01).unwrap();

        assert_eq!(circuit.psu_mode("psu1"), PsuMode::Cc);
        assert!((circuit.psu_current("psu1") - 1.0).abs() < 1e-9);
    }

    #[test]
    fn remote_sense_source_clamps_at_voltage_max_and_delivers_short_of_setpoint() {
        // The reproduction scenario: a 1 kΩ probe lead between the output stage and a 5 Ω
        // load, remote-sensed. Regulating 12 V at the load through a 1 A CC fold-back would
        // demand ~1005 V at source_node; the rated 60 V ceiling makes that impossible.
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 1000.0);
        attach_resistive(&mut circuit, "psu1", 5.0, 0.0);
        circuit.set_psu_voltage_setpoint("psu1", 12.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        circuit.set_psu_voltage_max("psu1", 60.0);
        circuit.set_psu_remote_sense("psu1", true);
        circuit.step(0.5).unwrap();

        assert_eq!(circuit.psu_mode("psu1"), PsuMode::Unreg);
        let v_load = circuit.psu_voltage("psu1");
        let i = circuit.psu_current("psu1");
        // source_node = v_load + i·r_series is pinned at the ceiling, not running away.
        let v_source = v_load + i * 1000.0;
        assert!(
            (v_source - 60.0).abs() < 1e-3,
            "source_node {v_source} vs ceiling 60"
        );
        // The delivered voltage and current honestly fall short of the 12 V / 1 A demand.
        let expected_i = 60.0 / 1005.0;
        assert!((i - expected_i).abs() < 1e-4, "current {i} vs {expected_i}");
        assert!(v_load < 12.0);
        assert!(i < 1.0);
    }

    #[test]
    fn raising_voltage_max_above_demand_leaves_unreg_for_normal_regulation() {
        // With the ceiling lifted well above what the lead drop demands, the same network
        // regulates normally (CC fold-back) instead of railing -- UNREG is genuinely the
        // ceiling binding, not an artifact of remote sense.
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 1000.0);
        attach_resistive(&mut circuit, "psu1", 5.0, 0.0);
        circuit.set_psu_voltage_setpoint("psu1", 12.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        circuit.set_psu_voltage_max("psu1", 5000.0);
        circuit.set_psu_remote_sense("psu1", true);
        circuit.step(0.5).unwrap();

        assert_eq!(circuit.psu_mode("psu1"), PsuMode::Cc);
        assert!((circuit.psu_current("psu1") - 1.0).abs() < 1e-4);
    }

    #[test]
    fn short_circuit_load_does_not_panic_or_produce_nan() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.0);
        attach_resistive(&mut circuit, "psu1", 0.0, 0.0);
        circuit.set_psu_voltage_setpoint("psu1", 5.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        circuit.step(0.01).unwrap();

        assert_eq!(circuit.psu_mode("psu1"), PsuMode::Cc);
        assert!(circuit.psu_current("psu1").is_finite());
        assert!(circuit.psu_voltage("psu1").is_finite());
        assert!(circuit.psu_current("psu1") > 0.0);
    }

    #[test]
    fn open_circuit_load_settles_to_near_zero_current() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.5);
        attach_resistive(&mut circuit, "psu1", f64::INFINITY, 0.0);
        circuit.set_psu_voltage_setpoint("psu1", 5.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        circuit.step(0.01).unwrap();

        assert_eq!(circuit.psu_mode("psu1"), PsuMode::Cv);
        assert!(circuit.psu_current("psu1").abs() < 1e-4);
        assert!((circuit.psu_voltage("psu1") - 5.0).abs() < 1e-3);
    }

    #[test]
    fn zero_elapsed_time_step_does_not_panic_or_produce_nan() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.5);
        attach_resistive(&mut circuit, "psu1", 1000.0, 0.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        circuit.set_psu_voltage_setpoint("psu1", 10.0);

        circuit.step(0.0).unwrap();
        circuit.step(0.0).unwrap();

        assert!(circuit.psu_voltage("psu1").is_finite());
        assert!(circuit.psu_current("psu1").is_finite());
    }

    #[test]
    fn capacitor_step_response_follows_the_analytic_rc_exponential() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.5);
        attach_resistive(&mut circuit, "psu1", 1000.0, 0.0);
        // A high current limit keeps this test in pure CV/RC territory throughout the
        // step -- the instantaneous demand right after a sharp setpoint step is large,
        // and current-limited fold-back during that demand spike is real PSU behavior,
        // but it is a *different*, separately-tested shape than the pure analytic
        // exponential this test checks.
        circuit.set_psu_current_limit("psu1", 50.0);
        circuit.set_psu_voltage_setpoint("psu1", 10.0);
        // Settle at v_initial before stepping the setpoint.
        circuit.step(0.01).unwrap();
        let v_initial = circuit.psu_voltage("psu1");

        circuit.set_psu_voltage_setpoint("psu1", 15.0);
        let r_series = 0.5;
        let r_load = 1000.0;
        let tau = (r_series * r_load / (r_series + r_load)) * DEFAULT_OUTPUT_CAPACITANCE_FARADS;
        let v_final = 15.0 * r_load / (r_series + r_load);

        let dt = 1e-5;
        let mut t = 0.0;
        for _ in 0..(5.0 * tau / dt) as usize {
            circuit.step(dt).unwrap();
            t += dt;
            let analytic = v_final - (v_final - v_initial) * (-t / tau).exp();
            let measured = circuit.psu_voltage("psu1");
            // Tolerance re-derived for backward Euler (design Correction 12): each call
            // takes STEP_SUBSTEPS implicit sub-solves of dt/10, whose first-order
            // discretization error accumulates to well under 10 mV on this 5 V step
            // (error factor ~ dt_sub/(2τ) ≈ 0.5% of the decaying term at its peak).
            assert!(
                (measured - analytic).abs() < 0.02,
                "at t={t}, measured {measured}V vs analytic {analytic}V"
            );
        }
    }

    #[test]
    fn reciprocal_coupling_is_a_no_op_not_a_representative_cycle() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "a", 0.5);
        new_enabled_psu(&mut circuit, "b", 0.5);
        circuit.set_psu_voltage_setpoint("a", 12.0);
        circuit.set_psu_current_limit("a", 0.2);
        circuit.set_psu_voltage_setpoint("b", 10.0);
        circuit.set_psu_current_limit("b", 5.0);
        circuit.set_psu_attachments("a", vec![Attachment::Coupled(InstrumentId::new("b"))]);
        // The reciprocal repoint must observe that b's bus is already the shared root
        // and do nothing, rather than forming a representative cycle.
        circuit.set_psu_attachments("b", vec![Attachment::Coupled(InstrumentId::new("a"))]);

        let n_before = circuit.n_unknowns;
        for _ in 0..50 {
            circuit.step(1e-4).unwrap();
        }
        assert_eq!(circuit.n_unknowns, n_before);
        assert!((circuit.psu_voltage("a") - circuit.psu_voltage("b")).abs() < 1e-9);
        assert!(circuit.psu_voltage("a").is_finite());
    }

    #[test]
    fn same_shape_reattachment_allocates_nothing_and_keeps_settled_state() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.5);
        attach_resistive(&mut circuit, "psu1", 1000.0, 0.0);
        circuit.set_psu_voltage_setpoint("psu1", 10.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        circuit.step(0.01).unwrap();
        let settled_voltage = circuit.psu_voltage("psu1");
        let n_before = circuit.n_unknowns;

        // The wiring layer re-attaches on every SCPI query (Correction 20): a same-shape
        // list must update parameters in place, allocating nothing and leaving the
        // capacitor's differential state untouched.
        attach_resistive(&mut circuit, "psu1", 1000.0, 0.0);
        assert_eq!(circuit.n_unknowns, n_before);
        assert_eq!(circuit.psu_voltage("psu1"), settled_voltage);

        // A same-shape parameter change takes effect on the next step.
        attach_resistive(&mut circuit, "psu1", 500.0, 0.0);
        assert_eq!(circuit.n_unknowns, n_before);
        circuit.step(0.01).unwrap();
        let expected_current = 10.0 / 500.5;
        assert!((circuit.psu_current("psu1") - expected_current).abs() < 1e-4);
    }

    #[test]
    fn cv_cc_mode_flip_across_steps_keeps_the_unknowns_layout_stable() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.5);
        attach_resistive(&mut circuit, "psu1", 0.1, 0.0);
        circuit.set_psu_voltage_setpoint("psu1", 5.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        circuit.step(0.01).unwrap();
        assert_eq!(circuit.psu_mode("psu1"), PsuMode::Cc);
        let n_in_cc = circuit.n_unknowns;

        // Raising the limit flips the source kind on the next step; both source kinds
        // own the same single branch slot (Correction 19a), so the layout is invariant.
        circuit.set_psu_current_limit("psu1", 100.0);
        circuit.step(0.01).unwrap();
        assert_eq!(circuit.psu_mode("psu1"), PsuMode::Cv);
        assert_eq!(circuit.n_unknowns, n_in_cc);
    }

    #[test]
    fn attachment_shape_change_reuses_freed_slots_and_never_grows_the_arena() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "a", 0.5);
        new_enabled_psu(&mut circuit, "b", 0.5);
        circuit.set_psu_voltage_setpoint("a", 12.0);
        circuit.set_psu_current_limit("a", 0.2);
        circuit.set_psu_voltage_setpoint("b", 10.0);
        circuit.set_psu_current_limit("b", 5.0);
        attach_resistive(&mut circuit, "a", 1000.0, 0.0);
        circuit.step(0.01).unwrap();
        let n_with_resistive = circuit.n_unknowns;

        // Resistive -> Coupled frees the resistive entry's own node+branch slots
        // (a Coupled entry owns no slots at all -- Correction 21)...
        circuit.set_psu_attachments("a", vec![Attachment::Coupled(InstrumentId::new("b"))]);
        assert_eq!(circuit.n_unknowns, n_with_resistive);
        assert_eq!(circuit.free_slots.len(), 2);
        circuit.step(0.01).unwrap();

        // ...and Coupled -> Resistive reuses them instead of growing (Correction 20).
        attach_resistive(&mut circuit, "a", 1000.0, 0.0);
        assert_eq!(circuit.n_unknowns, n_with_resistive);
        assert!(circuit.free_slots.is_empty());
        circuit.step(0.01).unwrap();
        let expected_current = 12.0 / 1000.5;
        assert!((circuit.psu_current("a") - expected_current).abs() < 1e-4);
    }

    #[test]
    fn repeated_coupled_reapply_allocates_nothing_and_is_idempotent() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "a", 0.5);
        new_enabled_psu(&mut circuit, "b", 0.5);
        circuit.set_psu_voltage_setpoint("a", 12.0);
        circuit.set_psu_current_limit("a", 0.2);
        circuit.set_psu_voltage_setpoint("b", 10.0);
        circuit.set_psu_current_limit("b", 5.0);
        circuit.set_psu_attachments("a", vec![Attachment::Coupled(InstrumentId::new("b"))]);
        let n_coupled = circuit.n_unknowns;
        let representatives = circuit.node_representative.clone();

        // The wiring layer's call-per-SCPI-query pattern (Correction 20/21): re-applying
        // the same Coupled list allocates nothing and leaves the map exactly as it was.
        for _ in 0..5 {
            circuit.set_psu_attachments("a", vec![Attachment::Coupled(InstrumentId::new("b"))]);
        }
        assert_eq!(circuit.n_unknowns, n_coupled);
        assert_eq!(circuit.node_representative, representatives);
        for _ in 0..50 {
            circuit.step(1e-4).unwrap();
        }
        assert!((circuit.psu_voltage("a") - circuit.psu_voltage("b")).abs() < 1e-9);
    }

    #[test]
    fn same_shape_parameter_update_mid_transient_keeps_the_capacitor_state() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.5);
        attach_resistive(&mut circuit, "psu1", 1000.0, 0.0);
        circuit.set_psu_current_limit("psu1", 50.0);
        circuit.set_psu_voltage_setpoint("psu1", 10.0);
        circuit.step(0.01).unwrap();

        // Kick off a transient and stop partway through it.
        circuit.set_psu_voltage_setpoint("psu1", 15.0);
        circuit.step(5e-5).unwrap();
        let mid_transient = circuit.psu_voltage("psu1");
        assert!(mid_transient > 10.1 && mid_transient < 14.9);

        // The wiring layer re-attaches on every SCPI query: the capacitor's differential
        // state at `bus` must survive it (Correction 20), so the transient continues
        // from where it was instead of resetting.
        attach_resistive(&mut circuit, "psu1", 1000.0, 0.0);
        assert_eq!(circuit.psu_voltage("psu1"), mid_transient);
        circuit.step(0.01).unwrap();
        let expected_current = 15.0 / 1000.5;
        assert!((circuit.psu_voltage("psu1") - (15.0 - expected_current * 0.5)).abs() < 1e-4);
    }

    #[test]
    fn disabled_psu_decays_toward_zero_through_the_load() {
        let mut circuit = Circuit::new();
        new_enabled_psu(&mut circuit, "psu1", 0.5);
        attach_resistive(&mut circuit, "psu1", 1000.0, 0.0);
        circuit.set_psu_voltage_setpoint("psu1", 10.0);
        circuit.set_psu_current_limit("psu1", 1.0);
        circuit.step(0.01).unwrap();
        let n_enabled = circuit.n_unknowns;

        // Correction 19c: disable stamps a zero-amp source in the same branch slot (no
        // early return, no layout change); the solved state decays through the load.
        circuit.set_psu_output_enabled("psu1", false);
        circuit.step(0.001).unwrap();
        assert_eq!(circuit.n_unknowns, n_enabled);
        assert!((circuit.psu_current("psu1")).abs() < 1e-9);
        assert!(circuit.psu_voltage("psu1") > 9.0);

        // Discharge tau is r_load * C = 0.22 s; a few seconds is fully decayed.
        for _ in 0..3 {
            circuit.step(1.0).unwrap();
        }
        assert!(circuit.psu_voltage("psu1").abs() < 0.01);
        assert!(circuit.psu_voltage("psu1").is_finite());
    }
}
