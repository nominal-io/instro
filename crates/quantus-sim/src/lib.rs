//! Config-driven simulator impersonating a Mecalc QuantusSeries device.
//!
//! Serves the QServer Q2.x REST plane (and, from Phase 3, the binary data
//! stream) from a rack-description config: chassis, slots, module types,
//! per-channel signal definitions. Fault-injection knobs land with the stream.
//!
//! Design constraint (PLAN.md D7): this crate's wire encoding is written
//! independently from quantus-client's parser — do not import parsing/encoding
//! logic from quantus-client. Response shapes come from docs/api-notes.md and
//! Mecalc's vendor-generated QProtocolCSharp; docs/assumptions.md tracks the
//! guessed parts.

pub mod config;
pub mod model;
pub mod rest;
pub mod stream;
pub mod templates;
