//! Client for Mecalc QuantusSeries DAQ mainframes (QServer Q2.x).
//!
//! Targets QServer Q2.x only (asserted via `GET /version` at connect). The
//! entry point is `blocking::QuantusClient::connect(RackConfig)` followed by
//! `reconcile()`; async consumers use `reconcile::Engine` directly. See the
//! repo's `docs/api-notes.md` for the protocol reference and `PLAN.md` for the
//! roadmap; the stream engine lands in Phase 3.

pub mod blocking;
pub mod config;
pub mod error;
pub mod reconcile;
pub mod rest;
pub mod settings;
pub mod stream;
pub mod wire;
