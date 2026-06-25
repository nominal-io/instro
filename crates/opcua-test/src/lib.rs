//! Test helpers for OPC UA.
//!
//! This crate is consumed as a dev-dependency from any layer that touches OPC UA.
//!
//! Two surfaces:
//!
//! - [`assert_roundtrip`] for native ↔ shim type-conversion tests.
//! - [`server`] — a declarative in-process [`TestServer`] backed by
//!   [`open62541`]'s server API. Use [`TestServer::builder`] to set up nodes,
//!   types, values, and access levels for end-to-end tests.

use std::fmt::Debug;

pub mod server;

// Re-export the relevant `open62541` surface so callers don't have to take a
// direct dependency on it just to construct variants and node ids.
pub use open62541::DataSource;
pub use open62541::DataSourceError;
pub use open62541::DataSourceReadContext;
pub use open62541::DataSourceResult;
pub use open62541::DataSourceWriteContext;
pub use open62541::MethodCallback;
pub use open62541::MethodCallbackContext;
pub use open62541::MethodCallbackResult;
pub use open62541::ua;
pub use server::Access;
pub use server::FolderSpec;
pub use server::MethodSpec;
pub use server::ParentRef;
pub use server::TestNodeId;
pub use server::TestServer;
pub use server::TestServerBuilder;
pub use server::ValueSource;
pub use server::VariableSpec;

#[track_caller]
pub fn assert_roundtrip<Native, Shim>(native: &Native, expected_shim: Shim)
where
    Native: Debug + PartialEq + TryFrom<Shim>,
    for<'a> Shim: Clone + Debug + PartialEq + TryFrom<&'a Native>,
    for<'a> <&'a Native as TryInto<Shim>>::Error: Debug,
    <Shim as TryInto<Native>>::Error: Debug,
{
    use std::any::type_name;

    #[expect(
        clippy::unwrap_used,
        reason = "panicking assertions expected to fail if shim -> native conversion fails"
    )]
    let shim = Shim::try_from(native).unwrap();

    assert_eq!(
        shim,
        expected_shim,
        "{}({:?}) -> {}({:?}) mismatch",
        type_name::<Native>(),
        native,
        type_name::<Shim>(),
        expected_shim,
    );

    #[expect(
        clippy::unwrap_used,
        reason = "panicking assertions expected to fail if native -> shim conversion fails"
    )]
    let back = Native::try_from(shim.clone()).unwrap();

    assert_eq!(
        back,
        *native,
        "{}({:?}) -> {}({:?}) mismatch",
        type_name::<Shim>(),
        shim,
        type_name::<Native>(),
        back,
    );
}
