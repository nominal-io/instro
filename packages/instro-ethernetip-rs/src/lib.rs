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

pub use error::Error;
pub use value::{StructuredValue, Value};

use std::future::Future;
use std::pin::Pin;

use rust_ethernet_ip::{EipClient, EtherNetIpError, PlcValue, RoutePath};

mod error;
#[cfg(test)]
mod mock_client;
mod value;

pub type Result<T> = std::result::Result<T, Error>;

/// Boxed future used for multithreaded runtime compatibility.
///
/// Thus, we need the explicit [`Send`] bound, which means this seam cannot use `async fn` in
/// the trait and instead returns a boxed future directly.
type ClientFuture<'a, T> =
    Pin<Box<dyn Future<Output = std::result::Result<T, EtherNetIpError>> + Send + 'a>>;

/// Private seam over [`EipClient`] for explicit tag operations and session teardown.
///
/// This stays 1:1 with [`EipClient`] so [`ExplicitSession`] can be unit-tested with a mock
/// client.
trait ExplicitClient: Send + Sync {
    fn read_tag<'a>(&'a mut self, tag_name: &'a str) -> ClientFuture<'a, PlcValue>;
    fn write_tag<'a>(&'a mut self, tag_name: &'a str, value: PlcValue) -> ClientFuture<'a, ()>;
    fn unregister_session<'a>(&'a mut self) -> ClientFuture<'a, ()>;
}

impl ExplicitClient for EipClient {
    fn read_tag<'a>(&'a mut self, tag_name: &'a str) -> ClientFuture<'a, PlcValue> {
        Box::pin(EipClient::read_tag(self, tag_name))
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

/// An active explicit-messaging EtherNet/IP session for a single target address.
///
/// Construct with [`ExplicitSession::connect`], use it for tag reads and writes, and call
/// [`ExplicitSession::close`] to unregister the session when finished. Dropping
/// [`ExplicitSession`] only drops the underlying transport; it does not perform the async
/// unregister handshake.
pub struct ExplicitSession {
    addr: String,
    client: Box<dyn ExplicitClient>,
}

impl ExplicitSession {
    /// Connect to an EtherNet/IP endpoint and register a session.
    ///
    /// `addr` must be parseable as a [`std::net::SocketAddr`] (for example `"192.168.1.10:44818"` or
    /// `"[::1]:44818"`). Hostnames such as `"plc.local:44818"` are not resolved here.
    /// Note that this implicitly registers a session with the target device on success.
    pub async fn connect(addr: &str) -> Result<Self> {
        Self::connect_with(addr, |addr| async move { EipClient::connect(&addr).await }).await
    }

    /// Connect to an EtherNet/IP endpoint through a backplane route path.
    ///
    /// The supplied slots are added to `rust-ethernet-ip`'s route path in order using
    /// `RoutePath::add_slot`. This follows the upstream route-path surface exactly: all slot hops
    /// are encoded before any future network hops.
    pub async fn connect_with_route_path_slots(addr: &str, slots: &[u8]) -> Result<Self> {
        if slots.is_empty() {
            return Self::connect(addr).await;
        }

        let route_path = route_path_from_slots(slots);
        Self::connect_with(addr, |addr| async move {
            EipClient::with_route_path(&addr, route_path).await
        })
        .await
    }

    // Abstracts the [`EipClient`] to allow for testing.
    async fn connect_with<C, F, Fut>(addr: &str, connect: F) -> Result<Self>
    where
        C: ExplicitClient + 'static,
        F: FnOnce(String) -> Fut,
        Fut: Future<Output = std::result::Result<C, EtherNetIpError>>,
    {
        let client = connect(addr.to_owned())
            .await
            .map_err(|source| Error::Connect {
                addr: addr.to_owned(),
                source: Box::new(source),
            })?;

        Ok(Self {
            addr: addr.to_owned(),
            client: Box::new(client),
        })
    }

    // Read the raw [`PlcValue`] for a tag.
    async fn read_tag_raw(&mut self, tag_name: &str) -> Result<PlcValue> {
        self.client
            .read_tag(tag_name)
            .await
            .map_err(|source| Error::ReadTag {
                addr: self.addr.clone(),
                tag_name: tag_name.to_owned(),
                source: Box::new(source),
            })
    }

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

    /// Read several tags sequentially, preserving input order in the returned list.
    pub async fn read_tags<S>(&mut self, tag_names: &[S]) -> Result<Vec<(String, Value)>>
    where
        S: AsRef<str>,
    {
        let mut values = Vec::with_capacity(tag_names.len());

        for tag_name in tag_names {
            let tag_name = tag_name.as_ref();
            let value = self.read_tag(tag_name).await?;
            values.push((tag_name.to_owned(), value));
        }

        Ok(values)
    }

    /// Write a user-facing [`Value`] to a PLC tag.
    pub async fn write_tag(&mut self, tag_name: &str, value: Value) -> Result<()> {
        let value: PlcValue = value.into();

        self.client
            .write_tag(tag_name, value)
            .await
            .map_err(|source| Error::WriteTag {
                addr: self.addr.clone(),
                tag_name: tag_name.to_owned(),
                source: Box::new(source),
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
        self.client
            .unregister_session()
            .await
            .map_err(|source| Error::Unregister {
                addr: self.addr,
                source: Box::new(source),
            })
    }
}

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

    use crate::mock_client::{MockClient, MockState};

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

    #[tokio::test]
    async fn connect_wraps_client_and_preserves_address() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let client = ExplicitSession::connect_with("10.0.0.5", |_| async {
            Ok(MockClient::new(state.clone(), vec![], vec![], Ok(())))
        })
        .await
        .expect("connect should succeed");

        assert_eq!(client.addr, "10.0.0.5");
    }

    #[tokio::test]
    async fn connect_wraps_connection_errors() {
        let result = ExplicitSession::connect_with("10.0.0.5", |_| async {
            let error = EtherNetIpError::Connection("refused".to_owned());
            Err::<MockClient, _>(error)
        })
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

    #[test]
    fn route_path_from_slots_adds_each_backplane_slot() {
        let route_path = route_path_from_slots(&[2, 0]);

        assert_eq!(route_path.slots, vec![2, 0]);
        assert_eq!(route_path.ports, Vec::<u8>::new());
        assert_eq!(route_path.addresses, Vec::<String>::new());
        assert_eq!(route_path.to_cip_bytes(), vec![0x01, 0x02, 0x01, 0x00]);
    }

    #[tokio::test]
    async fn read_tag_converts_plc_value_and_records_tag_name() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(
                state.clone(),
                vec![Ok(PlcValue::Dint(42))],
                vec![],
                Ok(()),
            )),
        };

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

    #[tokio::test]
    async fn read_tag_wraps_read_errors_with_context() {
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![Err(EtherNetIpError::TagNotFound("MissingTag".to_owned()))],
                vec![],
                Ok(()),
            )),
        };

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

    #[tokio::test]
    async fn read_tags_returns_values_in_input_order() {
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![
                    Ok(PlcValue::Bool(true)),
                    Ok(PlcValue::String("ok".to_owned())),
                ],
                vec![],
                Ok(()),
            )),
        };

        let values = session
            .read_tags(&["Running", "Status"])
            .await
            .expect("batch read should succeed");

        assert_eq!(
            values,
            vec![
                ("Running".to_owned(), Value::Bool(true)),
                ("Status".to_owned(), Value::String("ok".to_owned())),
            ]
        );
    }

    #[tokio::test]
    async fn read_tags_stops_after_first_error() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(
                state.clone(),
                vec![
                    Ok(PlcValue::Bool(true)),
                    Err(EtherNetIpError::ReadError {
                        status: 7,
                        message: "bad read".to_owned(),
                    }),
                ],
                vec![],
                Ok(()),
            )),
        };

        let error = session
            .read_tags(&["Running", "Status", "Ignored"])
            .await
            .expect_err("batch read should fail");

        match error {
            Error::ReadTag { tag_name, .. } => assert_eq!(tag_name, "Status"),
            other => panic!("unexpected error: {other:?}"),
        }

        assert_eq!(
            state.lock().expect("mock state poisoned").read_calls,
            vec!["Running".to_owned(), "Status".to_owned()]
        );
    }

    #[tokio::test]
    async fn write_tag_passes_through_value_and_tag_name() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(state.clone(), vec![], vec![Ok(())], Ok(()))),
        };

        session
            .write_tag("Setpoint", Value::Real(12.5))
            .await
            .expect("write should succeed");

        let locked = state.lock().expect("mock state poisoned");
        assert_eq!(locked.write_calls.len(), 1);
        assert_eq!(locked.write_calls[0].0, "Setpoint");
        assert!(matches!(locked.write_calls[0].1, PlcValue::Real(value) if value == 12.5));
    }

    #[tokio::test]
    async fn write_tag_wraps_write_errors_with_context() {
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![],
                vec![Err(EtherNetIpError::WriteError {
                    status: 8,
                    message: "denied".to_owned(),
                })],
                Ok(()),
            )),
        };

        let error = session
            .write_tag("Setpoint", Value::Int(5))
            .await
            .expect_err("write should fail");

        match error {
            Error::WriteTag {
                addr,
                tag_name,
                source,
            } => {
                assert_eq!(addr, "plc.local");
                assert_eq!(tag_name, "Setpoint");
                assert_eq!(source.to_string(), "Write error: denied (status: 8)");
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[tokio::test]
    async fn write_tag_struct_converts_typed_value() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(state.clone(), vec![], vec![Ok(())], Ok(()))),
        };

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

    #[tokio::test]
    async fn read_tag_struct_decodes_typed_value() {
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![Ok(PlcValue::Udt(rust_ethernet_ip::UdtData {
                    symbol_id: 11,
                    data: vec![9, 8, 7],
                }))],
                vec![],
                Ok(()),
            )),
        };

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

    #[tokio::test]
    async fn read_tag_struct_rejects_non_struct_value() {
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![Ok(PlcValue::Bool(true))],
                vec![],
                Ok(()),
            )),
        };

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

    #[tokio::test]
    async fn read_tag_struct_wraps_decode_errors_with_context() {
        let mut session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![Ok(PlcValue::Udt(rust_ethernet_ip::UdtData {
                    symbol_id: 11,
                    data: vec![],
                }))],
                vec![],
                Ok(()),
            )),
        };

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

    #[tokio::test]
    async fn close_unregisters_session() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(state.clone(), vec![], vec![], Ok(()))),
        };

        session.close().await.expect("close should succeed");

        assert_eq!(
            state.lock().expect("mock state poisoned").unregister_calls,
            1
        );
    }

    #[tokio::test]
    async fn close_wraps_unregister_errors_with_context() {
        let session = ExplicitSession {
            addr: "plc.local".to_owned(),
            client: Box::new(MockClient::new(
                Arc::new(Mutex::new(MockState::default())),
                vec![],
                vec![],
                Err(EtherNetIpError::ConnectionLost("socket closed".to_owned())),
            )),
        };

        let error = session.close().await.expect_err("close should fail");

        match error {
            Error::Unregister { addr, source } => {
                assert_eq!(addr, "plc.local");
                assert_eq!(source.to_string(), "Connection lost: socket closed");
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }
}
