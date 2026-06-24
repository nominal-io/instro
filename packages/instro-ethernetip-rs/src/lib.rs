//! Async EtherNet/IP explicit messaging for simple PLC tag reads and writes.
//!
//! This crate provides a small, crate-owned API around `rust-ethernet-ip` for the common
//! explicit-messaging workflow:
//!
//! - connect to one target with [`ExplicitSession::connect`]
//! - read one or more tags as crate-owned [`Value`]s
//! - write crate-owned [`Value`]s back to tags
//! - use [`ExplicitSession::read_tag_struct`] and [`ExplicitSession::write_tag_struct`] for
//!   caller-defined structured payloads
//! - explicitly unregister the session with [`ExplicitSession::close`]
//!
//! The public API intentionally hides backend transport types so callers can work with a
//! stable interface centered on [`ExplicitSession`], [`Value`], and [`StructuredValue`].
//!
//! # Recovery behavior
//!
//! The backend client automatically retries or falls back for some protocol-level transient
//! failures before an operation returns. If a read, batch read, or write still returns an
//! error that `rust_ethernet_ip::EtherNetIpError::is_retriable` marks retriable,
//! [`ExplicitSession`] drops the dead client. The failed operation is returned to the caller, and
//! the next read or write automatically reconnects with the same address and route path before
//! issuing the request.
//!
//! # Features
//!
//! By default, this crate exposes the async [`ExplicitSession`] API. Async callers provide their
//! own Tokio runtime by awaiting the session methods.
//!
//! Enable the `blocking` feature to add `blocking::ExplicitSession`, a synchronous wrapper around
//! the async session. The blocking API supports the same connect, read, write, structured-value,
//! and close operations, and it drives them on a private shared Tokio runtime.
//!
//! Do not call the blocking API from inside Tokio async code: it uses `Handle::block_on`, which
//! panics when invoked from an async execution context. Async callers should use the default
//! [`ExplicitSession`] API directly.
//!
//! # Examples
//!
//! Connect to a target, read a tag, write an updated value, then close the session:
//!
//! ```no_run
//! use instro_ethernetip_rs::{ExplicitSession, Result, Value};
//!
//! # fn main() -> Result<()> {
//! let runtime = tokio::runtime::Runtime::new().expect("runtime should build");
//! runtime.block_on(async {
//!     let mut session = ExplicitSession::connect("192.168.1.10:44818").await?;
//!
//!     let motor_running = session.read_tag("MotorRunning").await?;
//!     assert!(matches!(motor_running, Value::Bool(_)));
//!
//!     session.write_tag("CommandSpeed", 1_500_i32.into()).await?;
//!     session.close().await
//! })
//! # }
//! ```
//!
//! Convert Rust values into crate-owned PLC values without exposing backend types:
//!
//! ```
//! use instro_ethernetip_rs::Value;
//!
//! assert_eq!(Value::from(true), Value::Bool(true));
//! assert_eq!(Value::from(42_i32), Value::Dint(42));
//! assert_eq!(Value::from("ready"), Value::String("ready".to_owned()));
//! ```
//!
//! Preserve a user-defined type payload as opaque bytes with [`StructuredValue`]:
//!
//! ```
//! use instro_ethernetip_rs::{StructuredValue, Value};
//!
//! let payload = StructuredValue {
//!     symbol_id: Some(7),
//!     data: vec![0xde, 0xad, 0xbe, 0xef],
//! };
//!
//! assert_eq!(Value::from(payload.clone()), Value::Struct(payload));
//! ```
//!
#[cfg(feature = "blocking")]
pub mod blocking;

pub use error::{BatchReadError, Error};
pub use value::{StructuredValue, Value};

use std::future::Future;
use std::pin::Pin;

use rust_ethernet_ip::{BatchError, EipClient, EtherNetIpError, PlcValue, RoutePath};

mod error;
#[cfg(test)]
mod mock_client;
mod value;

pub type Result<T> = std::result::Result<T, Error>;

/// Boxed future used for multithreaded runtime compatibility.
///
/// The trait needs the explicit [`Send`] bound, which means this seam cannot use `async fn` in
/// the trait and instead returns a boxed future directly.
type ClientFuture<'a, T> =
    Pin<Box<dyn Future<Output = std::result::Result<T, EtherNetIpError>> + Send + 'a>>;

/// Boxed future returned by [`ExplicitConnector::connect`].
///
/// The future represents the in-flight connect operation and resolves to a newly connected
/// explicit client; it is not the long-lived connection itself.
type ConnectFuture<'a> = Pin<
    Box<
        dyn Future<Output = std::result::Result<Box<dyn ExplicitClient>, EtherNetIpError>>
            + Send
            + 'a,
    >,
>;

/// Private seam over [`EipClient`] for explicit tag operations and session teardown.
///
/// This stays 1:1 with [`EipClient`] so [`ExplicitSession`] can be unit-tested with a mock
/// client.
trait ExplicitClient: Send + Sync {
    fn read_tag<'a>(&'a mut self, tag_name: &'a str) -> ClientFuture<'a, PlcValue>;
    fn read_tags_batch<'a>(
        &'a mut self,
        tag_names: &'a [&'a str],
    ) -> ClientFuture<'a, Vec<(String, std::result::Result<PlcValue, BatchError>)>>;
    fn write_tag<'a>(&'a mut self, tag_name: &'a str, value: PlcValue) -> ClientFuture<'a, ()>;
    fn unregister_session<'a>(&'a mut self) -> ClientFuture<'a, ()>;
}

trait ExplicitConnector: Send + Sync {
    fn connect<'a>(&'a self, addr: &'a str, route_path_slots: &'a [u8]) -> ConnectFuture<'a>;
}

impl ExplicitClient for EipClient {
    fn read_tag<'a>(&'a mut self, tag_name: &'a str) -> ClientFuture<'a, PlcValue> {
        Box::pin(EipClient::read_tag(self, tag_name))
    }

    fn read_tags_batch<'a>(
        &'a mut self,
        tag_names: &'a [&'a str],
    ) -> ClientFuture<'a, Vec<(String, std::result::Result<PlcValue, BatchError>)>> {
        Box::pin(EipClient::read_tags_batch(self, tag_names))
    }

    fn write_tag<'a>(&'a mut self, tag_name: &'a str, value: PlcValue) -> ClientFuture<'a, ()> {
        Box::pin(EipClient::write_tag(self, tag_name, value))
    }

    // Note that "register" is omitted in this trait because free function `EipClient::connect` does
    // session registration implicitly.
    fn unregister_session<'a>(&'a mut self) -> ClientFuture<'a, ()> {
        Box::pin(EipClient::unregister_session(self))
    }
}

struct EipConnector;

impl ExplicitConnector for EipConnector {
    fn connect<'a>(&'a self, addr: &'a str, route_path_slots: &'a [u8]) -> ConnectFuture<'a> {
        Box::pin(async move {
            let client = if route_path_slots.is_empty() {
                EipClient::connect(addr).await?
            } else {
                let route_path = route_path_from_slots(route_path_slots);
                EipClient::with_route_path(addr, route_path).await?
            };

            Ok(Box::new(client) as Box<dyn ExplicitClient>)
        })
    }
}

/// An active explicit-messaging EtherNet/IP session for a single target address.
///
/// Construct with [`ExplicitSession::connect`], use it for tag reads and writes, and call
/// [`ExplicitSession::close`] to unregister the session when finished. Dropping
/// [`ExplicitSession`] only drops the underlying transport; it does not perform the async
/// unregister handshake. Some transient protocol failures are retried by the backend before an
/// operation returns; retryable errors that still escape mark the client disconnected so the next
/// operation reconnects automatically.
///
/// The session keeps the original target address and route path separate from the active backend
/// client. Retryable transport failures drop only the client; the next operation recreates it from
/// the saved connection identity.
pub struct ExplicitSession {
    addr: String,
    route_path_slots: Vec<u8>,
    /// Active backend client. `None` means reconnect before the next operation.
    client: Option<Box<dyn ExplicitClient>>,
    connector: Box<dyn ExplicitConnector>,
}

impl ExplicitSession {
    /// Connect to an EtherNet/IP endpoint and register a session.
    ///
    /// `addr` must be parseable as a [`std::net::SocketAddr`] (for example `"192.168.1.10:44818"` or
    /// `"[::1]:44818"`). Hostnames such as `"plc.local:44818"` are not resolved here.
    /// Note that this implicitly registers a session with the target device on success.
    pub async fn connect(addr: &str) -> Result<Self> {
        Self::connect_with_connector(addr, Vec::new(), Box::new(EipConnector)).await
    }

    /// Connect to an EtherNet/IP endpoint through a backplane route path.
    ///
    /// The supplied slots are added to `rust-ethernet-ip`'s route path in order using
    /// `RoutePath::add_slot`. This follows the upstream route-path surface exactly: all slot hops
    /// are encoded before any future network hops.
    pub async fn connect_with_route_path_slots(addr: &str, slots: &[u8]) -> Result<Self> {
        Self::connect_with_connector(addr, slots.to_vec(), Box::new(EipConnector)).await
    }

    async fn connect_with_connector(
        addr: &str,
        route_path_slots: Vec<u8>,
        connector: Box<dyn ExplicitConnector>,
    ) -> Result<Self> {
        let client = connector
            .connect(addr, &route_path_slots)
            .await
            .map_err(|source| Error::Connect {
                addr: addr.to_owned(),
                source: Box::new(source),
            })?;

        Ok(Self {
            addr: addr.to_owned(),
            route_path_slots,
            client: Some(client),
            connector,
        })
    }

    async fn reconnect_if_needed(&mut self) -> Result<()> {
        if self.client.is_some() {
            return Ok(());
        }

        let addr = self.addr.clone();
        let route_path_slots = self.route_path_slots.clone();
        let client = self
            .connector
            .connect(&addr, &route_path_slots)
            .await
            .map_err(|source| Error::Connect {
                addr: self.addr.clone(),
                source: Box::new(source),
            })?;
        self.client = Some(client);
        Ok(())
    }

    /// Mark the current client disconnected after retryable failures that survive backend retry.
    ///
    /// The failed operation is still reported to the caller, and the next operation will attempt
    /// to reconnect.
    fn drop_client_if_retriable(&mut self, source: &EtherNetIpError) {
        if source.is_retriable() {
            self.client = None;
        }
    }

    #[cfg(test)]
    fn new_for_test<C>(addr: &str, client: C) -> Self
    where
        C: ExplicitClient + 'static,
    {
        Self::new_for_test_with_connector(addr, Vec::new(), client, Box::new(EipConnector))
    }

    #[cfg(test)]
    fn new_for_test_with_connector<C>(
        addr: &str,
        route_path_slots: Vec<u8>,
        client: C,
        connector: Box<dyn ExplicitConnector>,
    ) -> Self
    where
        C: ExplicitClient + 'static,
    {
        Self {
            addr: addr.to_owned(),
            route_path_slots,
            client: Some(Box::new(client)),
            connector,
        }
    }

    /// Read the raw [`PlcValue`] for a tag.
    async fn read_tag_raw(&mut self, tag_name: &str) -> Result<PlcValue> {
        self.reconnect_if_needed().await?;
        let result = self
            .client
            .as_mut()
            .expect("client should be connected")
            .read_tag(tag_name)
            .await;

        result.map_err(|source| {
            self.drop_client_if_retriable(&source);
            Error::ReadTag {
                addr: self.addr.clone(),
                tag_name: tag_name.to_owned(),
                source: Box::new(source),
            }
        })
    }

    /// Read a [`Value`] for a tag.
    pub async fn read_tag(&mut self, tag_name: &str) -> Result<Value> {
        let value = self.read_tag_raw(tag_name).await?;
        Ok(value.into())
    }

    /// Read a structured tag and decode it into a caller-owned type.
    ///
    /// This is a convenience wrapper around [`ExplicitSession::read_tag`] for tags backed by
    /// user-defined types. Callers provide a [`TryFrom`] implementation from
    /// [`StructuredValue`].
    pub async fn read_tag_struct<T>(&mut self, tag_name: &str) -> Result<T>
    where
        T: TryFrom<StructuredValue>,
        T::Error: std::error::Error + Send + Sync + 'static,
    {
        let value = self.read_tag(tag_name).await?;
        let structured = match value {
            Value::Struct(value) => value,
            other => {
                return Err(Error::UnexpectedValueType {
                    addr: self.addr.clone(),
                    tag_name: tag_name.to_owned(),
                    actual_type: other.kind_name(),
                });
            }
        };

        structured
            .try_into()
            .map_err(|source| Error::DecodeStructuredTag {
                addr: self.addr.clone(),
                tag_name: tag_name.to_owned(),
                target_type: std::any::type_name::<T>(),
                source: Box::new(source),
            })
    }

    /// Read several tags in a single batch request, preserving input order in the returned list.
    ///
    /// Tag reads are sent to the PLC as a CIP Multiple Service Packet via the upstream batch
    /// API, which is significantly more efficient than issuing N separate reads. The upstream
    /// driver transparently chunks the request when the tag list exceeds packet limits.
    ///
    /// The outer [`Result`] reports transport-level failures (the whole batch could not be
    /// dispatched or its response could not be parsed). On success, the returned list contains
    /// one entry per requested tag in input order, with a per-tag [`Result`] so partial failures
    /// are first-class — a missing or type-mismatched tag does not prevent the other tags from
    /// being returned. Per-tag errors are wrapped as [`Error::BatchReadItem`], whose typed
    /// [`BatchReadError`] source preserves the upstream variant (tag-not-found, type mismatch,
    /// CIP error, etc.) for caller branching.
    pub async fn read_tags<S>(&mut self, tag_names: &[S]) -> Result<Vec<(String, Result<Value>)>>
    where
        S: AsRef<str>,
    {
        self.reconnect_if_needed().await?;
        let refs: Vec<&str> = tag_names.iter().map(AsRef::as_ref).collect();

        let batch = self
            .client
            .as_mut()
            .expect("client should be connected")
            .read_tags_batch(&refs)
            .await
            .map_err(|source| {
                self.drop_client_if_retriable(&source);
                Error::BatchRead {
                    addr: self.addr.clone(),
                    source: Box::new(source),
                }
            })?;

        let mut has_retriable_item_error = false;
        let values = batch
            .into_iter()
            .map(|(tag_name, result)| {
                let value = result.map(Value::from).map_err(|source| {
                    let source: BatchReadError = source.into();
                    has_retriable_item_error |= source.is_retriable();
                    Error::BatchReadItem {
                        addr: self.addr.clone(),
                        tag_name: tag_name.clone(),
                        source,
                    }
                });
                (tag_name, value)
            })
            .collect();

        if has_retriable_item_error {
            self.client = None;
        }

        Ok(values)
    }

    /// Write a user-facing [`Value`] to a PLC tag.
    pub async fn write_tag(&mut self, tag_name: &str, value: Value) -> Result<()> {
        self.reconnect_if_needed().await?;
        let value: PlcValue = value.into();

        self.client
            .as_mut()
            .expect("client should be connected")
            .write_tag(tag_name, value)
            .await
            .map_err(|source| {
                self.drop_client_if_retriable(&source);
                Error::WriteTag {
                    addr: self.addr.clone(),
                    tag_name: tag_name.to_owned(),
                    source: Box::new(source),
                }
            })
    }

    /// Encode a caller-owned type into a [`StructuredValue`] and write it to a tag.
    ///
    /// This is a convenience wrapper around [`ExplicitSession::write_tag`] for structured PLC
    /// payloads. Callers provide an [`Into`] conversion to [`StructuredValue`].
    pub async fn write_tag_struct<T>(&mut self, tag_name: &str, value: T) -> Result<()>
    where
        T: Into<StructuredValue>,
    {
        self.write_tag(tag_name, Value::Struct(value.into())).await
    }

    /// Unregister the explicit EtherNet/IP session.
    ///
    /// Call this before dropping [`ExplicitSession`] when you want graceful protocol-level
    /// cleanup.
    /// If unregister fails, `self` is still consumed and the caller cannot retry; that is
    /// acceptable here because the underlying connection is likely already broken anyway.
    pub async fn close(mut self) -> Result<()> {
        let Some(mut client) = self.client.take() else {
            return Ok(());
        };

        client
            .unregister_session()
            .await
            .map_err(|source| Error::Unregister {
                addr: self.addr,
                source: Box::new(source),
            })
    }
}

// Intentionally not a From impl for clarity
fn route_path_from_slots(slots: &[u8]) -> RoutePath {
    slots.iter().fold(RoutePath::new(), |route_path, slot| {
        route_path.add_slot(*slot)
    })
}

impl Value {
    /// Internal only for error messages/labels
    fn kind_name(&self) -> &'static str {
        match self {
            Self::Bool(_) => "bool",
            Self::Sint(_) => "sint",
            Self::Int(_) => "int",
            Self::Dint(_) => "dint",
            Self::Lint(_) => "lint",
            Self::Usint(_) => "usint",
            Self::Uint(_) => "uint",
            Self::Udint(_) => "udint",
            Self::Ulint(_) => "ulint",
            Self::Real(_) => "real",
            Self::Lreal(_) => "lreal",
            Self::String(_) => "string",
            Self::Struct(_) => "struct",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::error::Error as StdError;
    use std::fmt;
    use std::sync::{Arc, Mutex};

    use rust_ethernet_ip::PlcValue;

    use crate::mock_client::{
        BatchReadResult, MockClient, MockConnector, MockConnectorState, MockState,
    };

    #[derive(Debug, PartialEq, Eq)]
    struct ExampleStruct {
        bytes: Vec<u8>,
    }

    impl From<ExampleStruct> for StructuredValue {
        fn from(value: ExampleStruct) -> Self {
            Self {
                symbol_id: Some(11),
                data: value.bytes,
            }
        }
    }

    #[derive(Debug, PartialEq, Eq)]
    struct DecodeExampleStructError(&'static str);

    impl fmt::Display for DecodeExampleStructError {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            f.write_str(self.0)
        }
    }

    impl StdError for DecodeExampleStructError {}

    impl TryFrom<StructuredValue> for ExampleStruct {
        type Error = DecodeExampleStructError;

        fn try_from(value: StructuredValue) -> std::result::Result<Self, Self::Error> {
            if value.data.is_empty() {
                return Err(DecodeExampleStructError("expected non-empty payload"));
            }

            Ok(Self { bytes: value.data })
        }
    }

    fn retryable_connection_error() -> EtherNetIpError {
        EtherNetIpError::Connection("socket closed".to_owned())
    }

    fn mock_client_with_results(
        read_results: Vec<std::result::Result<PlcValue, EtherNetIpError>>,
        batch_read_results: Vec<BatchReadResult>,
        write_results: Vec<std::result::Result<(), EtherNetIpError>>,
    ) -> MockClient {
        MockClient::new(
            Arc::new(Mutex::new(MockState::default())),
            read_results,
            write_results,
            Ok(()),
        )
        .with_batch_read_results(batch_read_results)
    }

    /// Verifies that a successful connector result becomes an active session with the requested address.
    #[tokio::test]
    async fn connect_wraps_client_and_preserves_address() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let client = ExplicitSession::connect_with_connector(
            "10.0.0.5",
            Vec::new(),
            Box::new(MockConnector::new(
                connector_state.clone(),
                vec![Ok(MockClient::new(state.clone(), vec![], vec![], Ok(())))],
            )),
        )
        .await
        .expect("connect should succeed");

        assert_eq!(client.addr, "10.0.0.5");
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls,
            vec![("10.0.0.5".to_owned(), Vec::<u8>::new())]
        );
    }

    /// Verifies that connection failures are returned with the target address in the public error.
    #[tokio::test]
    async fn connect_wraps_connection_errors() {
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let result = ExplicitSession::connect_with_connector(
            "10.0.0.5",
            Vec::new(),
            Box::new(MockConnector::new(
                connector_state,
                vec![Err(EtherNetIpError::Connection("refused".to_owned()))],
            )),
        )
        .await;
        let error = match result {
            Ok(_) => panic!("connect should fail"),
            Err(error) => error,
        };

        match error {
            Error::Connect { addr, source } => {
                assert_eq!(addr, "10.0.0.5");
                assert_eq!(source.to_string(), "Connection error: refused");
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    /// Verifies that slot numbers are encoded into the upstream backplane route path in order.
    #[test]
    fn route_path_from_slots_adds_each_backplane_slot() {
        let route_path = route_path_from_slots(&[2, 0]);

        assert_eq!(route_path.slots, vec![2, 0]);
        assert_eq!(route_path.ports, Vec::<u8>::new());
        assert_eq!(route_path.addresses, Vec::<String>::new());
        assert_eq!(route_path.to_cip_bytes(), vec![0x01, 0x02, 0x01, 0x00]);
    }

    /// Verifies that a raw PLC value is converted to the public value type and the tag is sent unchanged.
    #[tokio::test]
    async fn read_tag_converts_plc_value_and_records_tag_name() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(state.clone(), vec![Ok(PlcValue::Dint(42))], vec![], Ok(())),
        );

        let value = session
            .read_tag("MotorSpeed")
            .await
            .expect("read should succeed");

        assert_eq!(value, Value::Dint(42));
        assert_eq!(
            state.lock().expect("mock state poisoned").read_calls,
            vec!["MotorSpeed".to_owned()]
        );
    }

    /// Verifies that read errors keep the address, tag name, and backend error in the public error.
    #[tokio::test]
    async fn read_tag_wraps_read_errors_with_context() {
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![Err(EtherNetIpError::TagNotFound("MissingTag".to_owned()))],
                vec![],
                Ok(()),
            ),
        );

        let error = session
            .read_tag("MissingTag")
            .await
            .expect_err("read should fail");

        match error {
            Error::ReadTag {
                addr,
                tag_name,
                source,
            } => {
                assert_eq!(addr, "plc.local");
                assert_eq!(tag_name, "MissingTag");
                assert_eq!(source.to_string(), "Tag not found: MissingTag");
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    /// Verifies that a retryable read error is surfaced once and the next read reconnects automatically.
    #[tokio::test]
    async fn retryable_read_error_disconnects_and_next_read_reconnects() {
        let first_state = Arc::new(Mutex::new(MockState::default()));
        let reconnected_state = Arc::new(Mutex::new(MockState::default()));
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let connector = MockConnector::new(
            connector_state.clone(),
            vec![Ok(MockClient::new(
                reconnected_state.clone(),
                vec![Ok(PlcValue::Dint(7))],
                vec![],
                Ok(()),
            ))],
        );
        let mut session = ExplicitSession::new_for_test_with_connector(
            "plc.local",
            vec![2, 0],
            MockClient::new(
                first_state.clone(),
                vec![Err(EtherNetIpError::ConnectionLost(
                    "socket closed".to_owned(),
                ))],
                vec![],
                Ok(()),
            ),
            Box::new(connector),
        );

        let error = session
            .read_tag("Speed")
            .await
            .expect_err("first read should report the dropped connection");
        match error {
            Error::ReadTag {
                addr,
                tag_name,
                source,
            } => {
                assert_eq!(addr, "plc.local");
                assert_eq!(tag_name, "Speed");
                assert_eq!(source.to_string(), "Connection lost: socket closed");
            }
            other => panic!("unexpected error: {other:?}"),
        }

        let value = session
            .read_tag("Speed")
            .await
            .expect("second read should reconnect and succeed");

        assert_eq!(value, Value::Dint(7));
        assert_eq!(
            first_state.lock().expect("mock state poisoned").read_calls,
            vec!["Speed".to_owned()]
        );
        assert_eq!(
            reconnected_state
                .lock()
                .expect("mock state poisoned")
                .read_calls,
            vec!["Speed".to_owned()]
        );
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls,
            vec![("plc.local".to_owned(), vec![2, 0])]
        );
    }

    /// Verifies that repeated retryable read failures can reconnect until a later success.
    #[tokio::test]
    async fn retryable_read_errors_can_repeat_before_success() {
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let client = |result| mock_client_with_results(vec![result], vec![], vec![]);
        let mut session = ExplicitSession::new_for_test_with_connector(
            "plc.local",
            Vec::new(),
            client(Err(retryable_connection_error())),
            Box::new(MockConnector::new(
                connector_state.clone(),
                [
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Ok(PlcValue::Dint(7)))),
                ]
                .into(),
            )),
        );

        for _ in 0..5 {
            session
                .read_tag("Speed")
                .await
                .expect_err("retryable failure should be returned");
        }

        assert_eq!(
            session
                .read_tag("Speed")
                .await
                .expect("sixth read should reconnect and succeed"),
            Value::Dint(7)
        );
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls
                .len(),
            5
        );
    }

    /// Verifies that non-retryable read errors leave the current client connected for later calls.
    #[tokio::test]
    async fn non_retryable_read_error_keeps_existing_client() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let mut session = ExplicitSession::new_for_test_with_connector(
            "plc.local",
            Vec::new(),
            MockClient::new(
                state.clone(),
                vec![
                    Err(EtherNetIpError::TagNotFound("MissingTag".to_owned())),
                    Ok(PlcValue::Dint(11)),
                ],
                vec![],
                Ok(()),
            ),
            Box::new(MockConnector::new(connector_state.clone(), vec![])),
        );

        session
            .read_tag("MissingTag")
            .await
            .expect_err("first read should fail without disconnecting");

        let value = session
            .read_tag("NextTag")
            .await
            .expect("same client should handle the next read");

        assert_eq!(value, Value::Dint(11));
        assert_eq!(
            state.lock().expect("mock state poisoned").read_calls,
            vec!["MissingTag".to_owned(), "NextTag".to_owned()]
        );
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls,
            Vec::<(String, Vec<u8>)>::new()
        );
    }

    /// Verifies that batch reads make one backend batch call and preserve the caller's tag order.
    #[tokio::test]
    async fn read_tags_issues_single_batch_call_and_returns_values_in_input_order() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(state.clone(), vec![], vec![], Ok(())).with_batch_read_results(vec![
                Ok(vec![
                    Ok(PlcValue::Bool(true)),
                    Ok(PlcValue::String("ok".to_owned())),
                ]),
            ]),
        );

        let values = session
            .read_tags(&["Running", "Status"])
            .await
            .expect("batch read should succeed");

        assert_eq!(values.len(), 2);
        assert_eq!(values[0].0, "Running");
        assert_eq!(
            values[0].1.as_ref().expect("first read should succeed"),
            &Value::Bool(true)
        );
        assert_eq!(values[1].0, "Status");
        assert_eq!(
            values[1].1.as_ref().expect("second read should succeed"),
            &Value::String("ok".to_owned())
        );

        let locked = state.lock().expect("mock state poisoned");
        assert_eq!(locked.read_calls, Vec::<String>::new());
        assert_eq!(
            locked.batch_read_calls,
            vec![vec!["Running".to_owned(), "Status".to_owned()]]
        );
    }

    /// Verifies that per-tag batch failures are returned beside successful values instead of failing the whole call.
    #[tokio::test]
    async fn read_tags_surfaces_per_tag_errors_alongside_successes() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(state.clone(), vec![], vec![], Ok(())).with_batch_read_results(vec![
                Ok(vec![
                    Ok(PlcValue::Bool(true)),
                    Err(rust_ethernet_ip::BatchError::TagNotFound(
                        "Status".to_owned(),
                    )),
                    Ok(PlcValue::Dint(7)),
                ]),
            ]),
        );

        let values = session
            .read_tags(&["Running", "Status", "Counter"])
            .await
            .expect("batch read should succeed at transport level");

        assert_eq!(values.len(), 3);
        assert_eq!(values[0].0, "Running");
        assert!(values[0].1.is_ok());

        match &values[1].1 {
            Err(Error::BatchReadItem {
                addr,
                tag_name,
                source,
            }) => {
                assert_eq!(addr, "plc.local");
                assert_eq!(tag_name, "Status");
                assert!(matches!(source, BatchReadError::TagNotFound(t) if t == "Status"));
            }
            other => panic!("expected per-tag BatchReadItem error, got {other:?}"),
        }

        assert_eq!(values[2].0, "Counter");
        assert_eq!(
            values[2].1.as_ref().expect("third read should succeed"),
            &Value::Dint(7)
        );
    }

    /// Verifies that a batch data-type mismatch is preserved as the typed public batch error variant.
    #[tokio::test]
    async fn read_tags_preserves_data_type_mismatch_variant() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(state.clone(), vec![], vec![], Ok(())).with_batch_read_results(vec![
                Ok(vec![Err(rust_ethernet_ip::BatchError::DataTypeMismatch {
                    expected: "DINT".to_owned(),
                    actual: "REAL".to_owned(),
                })]),
            ]),
        );

        let values = session
            .read_tags(&["Counter"])
            .await
            .expect("batch read should succeed at transport level");

        match &values[0].1 {
            Err(Error::BatchReadItem { source, .. }) => match source {
                BatchReadError::DataTypeMismatch { expected, actual } => {
                    assert_eq!(expected, "DINT");
                    assert_eq!(actual, "REAL");
                }
                other => panic!("expected DataTypeMismatch, got {other:?}"),
            },
            other => panic!("expected per-tag BatchReadItem error, got {other:?}"),
        }
    }

    /// Verifies that a batch CIP error is preserved as the typed public batch error variant.
    #[tokio::test]
    async fn read_tags_preserves_cip_error_variant() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(state.clone(), vec![], vec![], Ok(())).with_batch_read_results(vec![
                Ok(vec![Err(rust_ethernet_ip::BatchError::CipError {
                    status: 0x04,
                    message: "path segment error".to_owned(),
                })]),
            ]),
        );

        let values = session
            .read_tags(&["Counter"])
            .await
            .expect("batch read should succeed at transport level");

        match &values[0].1 {
            Err(Error::BatchReadItem { source, .. }) => match source {
                BatchReadError::Cip { status, message } => {
                    assert_eq!(*status, 0x04);
                    assert_eq!(message, "path segment error");
                }
                other => panic!("expected Cip variant, got {other:?}"),
            },
            other => panic!("expected per-tag BatchReadItem error, got {other:?}"),
        }
    }

    /// Verifies that per-tag batch network failures mark the current client disconnected.
    #[tokio::test]
    async fn retryable_batch_item_error_disconnects_and_next_batch_reconnects() {
        let first_state = Arc::new(Mutex::new(MockState::default()));
        let reconnected_state = Arc::new(Mutex::new(MockState::default()));
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let reconnected_client = MockClient::new(reconnected_state.clone(), vec![], vec![], Ok(()))
            .with_batch_read_results(vec![Ok(vec![Ok(PlcValue::Dint(25))])]);
        let mut session = ExplicitSession::new_for_test_with_connector(
            "plc.local",
            Vec::new(),
            MockClient::new(first_state.clone(), vec![], vec![], Ok(())).with_batch_read_results(
                vec![Ok(vec![Err(rust_ethernet_ip::BatchError::NetworkError(
                    "IO error: Broken pipe (os error 32)".to_owned(),
                ))])],
            ),
            Box::new(MockConnector::new(
                connector_state.clone(),
                vec![Ok(reconnected_client)],
            )),
        );

        let values = session
            .read_tags(&["test_dint"])
            .await
            .expect("per-tag batch failure should not fail the outer batch call");
        match &values[0].1 {
            Err(Error::BatchReadItem { source, .. }) => {
                assert!(
                    matches!(source, BatchReadError::Network(message) if message.contains("Broken pipe"))
                );
            }
            other => panic!("expected per-tag network error, got {other:?}"),
        }

        let values = session
            .read_tags(&["test_dint"])
            .await
            .expect("next batch should reconnect and succeed");

        assert_eq!(
            values[0].1.as_ref().expect("tag should succeed"),
            &Value::Dint(25)
        );
        assert_eq!(
            first_state
                .lock()
                .expect("mock state poisoned")
                .batch_read_calls,
            vec![vec!["test_dint".to_owned()]]
        );
        assert_eq!(
            reconnected_state
                .lock()
                .expect("mock state poisoned")
                .batch_read_calls,
            vec![vec!["test_dint".to_owned()]]
        );
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls,
            vec![("plc.local".to_owned(), Vec::<u8>::new())]
        );
    }

    /// Verifies that a retryable batch-read failure is surfaced once and the next batch reconnects automatically.
    #[tokio::test]
    async fn retryable_batch_read_error_disconnects_and_next_batch_reconnects() {
        let first_state = Arc::new(Mutex::new(MockState::default()));
        let reconnected_state = Arc::new(Mutex::new(MockState::default()));
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let reconnected_client = MockClient::new(reconnected_state.clone(), vec![], vec![], Ok(()))
            .with_batch_read_results(vec![Ok(vec![Ok(PlcValue::Bool(true))])]);
        let mut session = ExplicitSession::new_for_test_with_connector(
            "plc.local",
            Vec::new(),
            MockClient::new(first_state.clone(), vec![], vec![], Ok(())).with_batch_read_results(
                vec![Err(EtherNetIpError::Connection(
                    "batch socket closed".to_owned(),
                ))],
            ),
            Box::new(MockConnector::new(
                connector_state.clone(),
                vec![Ok(reconnected_client)],
            )),
        );

        let error = session
            .read_tags(&["Running"])
            .await
            .expect_err("first batch should report the dropped connection");
        match error {
            Error::BatchRead { addr, source } => {
                assert_eq!(addr, "plc.local");
                assert_eq!(source.to_string(), "Connection error: batch socket closed");
            }
            other => panic!("unexpected error: {other:?}"),
        }

        let values = session
            .read_tags(&["Running"])
            .await
            .expect("second batch should reconnect and succeed");

        assert_eq!(
            values[0].1.as_ref().expect("tag should succeed"),
            &Value::Bool(true)
        );
        assert_eq!(
            first_state
                .lock()
                .expect("mock state poisoned")
                .batch_read_calls,
            vec![vec!["Running".to_owned()]]
        );
        assert_eq!(
            reconnected_state
                .lock()
                .expect("mock state poisoned")
                .batch_read_calls,
            vec![vec!["Running".to_owned()]]
        );
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls,
            vec![("plc.local".to_owned(), Vec::<u8>::new())]
        );
    }

    /// Verifies that repeated retryable batch failures can reconnect until a later success.
    #[tokio::test]
    async fn retryable_batch_read_errors_can_repeat_before_success() {
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let client =
            |result: BatchReadResult| mock_client_with_results(vec![], vec![result], vec![]);
        let mut session = ExplicitSession::new_for_test_with_connector(
            "plc.local",
            Vec::new(),
            client(Err(retryable_connection_error())),
            Box::new(MockConnector::new(
                connector_state.clone(),
                [
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Ok(vec![Ok(PlcValue::Bool(true))]))),
                ]
                .into(),
            )),
        );

        for _ in 0..5 {
            session
                .read_tags(&["Running"])
                .await
                .expect_err("retryable failure should be returned");
        }

        let values = session
            .read_tags(&["Running"])
            .await
            .expect("sixth batch should reconnect and succeed");

        assert_eq!(
            values[0].1.as_ref().expect("tag should succeed"),
            &Value::Bool(true)
        );
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls
                .len(),
            5
        );
    }

    /// Verifies that writes pass the caller's tag name and converted value to the backend unchanged.
    #[tokio::test]
    async fn write_tag_passes_through_value_and_tag_name() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(state.clone(), vec![], vec![Ok(())], Ok(())),
        );

        session
            .write_tag("Setpoint", Value::Real(12.5))
            .await
            .expect("write should succeed");

        let locked = state.lock().expect("mock state poisoned");
        assert_eq!(locked.write_calls.len(), 1);
        assert_eq!(locked.write_calls[0].0, "Setpoint");
        assert!(matches!(locked.write_calls[0].1, PlcValue::Real(value) if value == 12.5));
    }

    /// Regression guard for retryable write failures: the failed write is reported, the same call is not issued again by this wrapper, and the next operation reconnects automatically.
    #[tokio::test]
    async fn retryable_write_error_disconnects_and_next_operation_reconnects() {
        let first_state = Arc::new(Mutex::new(MockState::default()));
        let reconnected_state = Arc::new(Mutex::new(MockState::default()));
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let mut session = ExplicitSession::new_for_test_with_connector(
            "plc.local",
            Vec::new(),
            MockClient::new(
                first_state.clone(),
                vec![],
                vec![Err(EtherNetIpError::Connection("write failed".to_owned()))],
                Ok(()),
            ),
            Box::new(MockConnector::new(
                connector_state.clone(),
                vec![Ok(MockClient::new(
                    reconnected_state.clone(),
                    vec![Ok(PlcValue::Dint(12))],
                    vec![],
                    Ok(()),
                ))],
            )),
        );

        let error = session
            .write_tag("Setpoint", Value::Dint(12))
            .await
            .expect_err("write should report the dropped connection");
        match error {
            Error::WriteTag {
                addr,
                tag_name,
                source,
            } => {
                assert_eq!(addr, "plc.local");
                assert_eq!(tag_name, "Setpoint");
                assert_eq!(source.to_string(), "Connection error: write failed");
            }
            other => panic!("unexpected error: {other:?}"),
        }

        let value = session
            .read_tag("Setpoint")
            .await
            .expect("next operation should reconnect");

        assert_eq!(value, Value::Dint(12));
        assert_eq!(
            first_state
                .lock()
                .expect("mock state poisoned")
                .write_calls
                .len(),
            1
        );
        assert_eq!(
            reconnected_state
                .lock()
                .expect("mock state poisoned")
                .read_calls,
            vec!["Setpoint".to_owned()]
        );
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls,
            vec![("plc.local".to_owned(), Vec::<u8>::new())]
        );
    }

    /// Verifies that repeated retryable write failures can reconnect until a later success.
    #[tokio::test]
    async fn retryable_write_errors_can_repeat_before_success() {
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let client = |result| mock_client_with_results(vec![], vec![], vec![result]);
        let mut session = ExplicitSession::new_for_test_with_connector(
            "plc.local",
            Vec::new(),
            client(Err(retryable_connection_error())),
            Box::new(MockConnector::new(
                connector_state.clone(),
                [
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Err(retryable_connection_error()))),
                    Ok(client(Ok(()))),
                ]
                .into(),
            )),
        );

        for _ in 0..5 {
            session
                .write_tag("Setpoint", Value::Dint(12))
                .await
                .expect_err("retryable failure should be returned");
        }

        session
            .write_tag("Setpoint", Value::Dint(12))
            .await
            .expect("sixth write should reconnect and succeed");
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls
                .len(),
            5
        );
    }

    /// Verifies that a failed automatic reconnect is reported as a connection error for the same address.
    #[tokio::test]
    async fn reconnect_failure_is_returned_as_connect_error() {
        let connector_state = Arc::new(Mutex::new(MockConnectorState::default()));
        let mut session = ExplicitSession::new_for_test_with_connector(
            "plc.local",
            Vec::new(),
            MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![Err(EtherNetIpError::Connection("dropped".to_owned()))],
                vec![],
                Ok(()),
            ),
            Box::new(MockConnector::new(
                connector_state.clone(),
                vec![Err(EtherNetIpError::Connection("still down".to_owned()))],
            )),
        );

        session
            .read_tag("Speed")
            .await
            .expect_err("first read should mark the client disconnected");

        let error = session
            .read_tag("Speed")
            .await
            .expect_err("reconnect should fail clearly");

        match error {
            Error::Connect { addr, source } => {
                assert_eq!(addr, "plc.local");
                assert_eq!(source.to_string(), "Connection error: still down");
            }
            other => panic!("unexpected error: {other:?}"),
        }
        assert_eq!(
            connector_state
                .lock()
                .expect("mock connector state poisoned")
                .connect_calls,
            vec![("plc.local".to_owned(), Vec::<u8>::new())]
        );
    }

    /// Verifies that structured writes encode the caller-owned type before sending it to the backend.
    #[tokio::test]
    async fn write_tag_struct_converts_typed_value() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(state.clone(), vec![], vec![Ok(())], Ok(())),
        );

        session
            .write_tag_struct(
                "Recipe",
                ExampleStruct {
                    bytes: vec![1, 2, 3],
                },
            )
            .await
            .expect("write should succeed");

        let locked = state.lock().expect("mock state poisoned");
        assert_eq!(locked.write_calls.len(), 1);
        assert_eq!(locked.write_calls[0].0, "Recipe");
        assert_eq!(
            locked.write_calls[0].1,
            PlcValue::Udt(rust_ethernet_ip::UdtData {
                symbol_id: 11,
                data: vec![1, 2, 3],
            })
        );
    }

    /// Verifies that structured reads decode backend UDT bytes into the caller-owned target type.
    #[tokio::test]
    async fn read_tag_struct_decodes_typed_value() {
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![Ok(PlcValue::Udt(rust_ethernet_ip::UdtData {
                    symbol_id: 11,
                    data: vec![9, 8, 7],
                }))],
                vec![],
                Ok(()),
            ),
        );

        let value: ExampleStruct = session
            .read_tag_struct("Recipe")
            .await
            .expect("read should succeed");

        assert_eq!(
            value,
            ExampleStruct {
                bytes: vec![9, 8, 7]
            }
        );
    }

    /// Verifies that structured reads reject scalar PLC values before attempting user decoding.
    #[tokio::test]
    async fn read_tag_struct_rejects_non_struct_value() {
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![Ok(PlcValue::Bool(true))],
                vec![],
                Ok(()),
            ),
        );

        let error = session
            .read_tag_struct::<ExampleStruct>("Recipe")
            .await
            .expect_err("read should fail");

        match error {
            Error::UnexpectedValueType {
                addr,
                tag_name,
                actual_type,
            } => {
                assert_eq!(addr, "plc.local");
                assert_eq!(tag_name, "Recipe");
                assert_eq!(actual_type, "bool");
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    /// Verifies that structured decode failures include the address, tag name, and target type.
    #[tokio::test]
    async fn read_tag_struct_wraps_decode_errors_with_context() {
        let mut session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![Ok(PlcValue::Udt(rust_ethernet_ip::UdtData {
                    symbol_id: 11,
                    data: vec![],
                }))],
                vec![],
                Ok(()),
            ),
        );

        let error = session
            .read_tag_struct::<ExampleStruct>("Recipe")
            .await
            .expect_err("decode should fail");

        match error {
            Error::DecodeStructuredTag {
                addr,
                tag_name,
                target_type,
                source,
            } => {
                assert_eq!(addr, "plc.local");
                assert_eq!(tag_name, "Recipe");
                assert!(target_type.ends_with("ExampleStruct"));
                assert_eq!(source.to_string(), "expected non-empty payload");
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    /// Verifies that closing a session unregisters it through the backend client.
    #[tokio::test]
    async fn close_unregisters_session() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let session = ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(state.clone(), vec![], vec![], Ok(())),
        );

        session.close().await.expect("close should succeed");

        assert_eq!(
            state.lock().expect("mock state poisoned").unregister_calls,
            1
        );
    }
}
