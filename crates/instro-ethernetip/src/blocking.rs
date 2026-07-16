//! Blocking wrapper around the async explicit EtherNet/IP session API.
//!
//! Enable the `blocking` feature to use this module from synchronous Rust code. All blocking
//! sessions share one private Tokio runtime. Async callers should use [`crate::ExplicitSession`]
//! directly.

use std::sync::OnceLock;
use std::sync::atomic::{AtomicUsize, Ordering};

use thiserror::Error as ThisError;
use tokio::runtime::{Builder, Handle, Runtime};

use crate::{Error, Result, StructuredValue, Value};

#[derive(Debug, Clone, ThisError)]
#[error("{0}")]
struct RuntimeInitError(String);

/// Return a handle to the private runtime shared by all blocking sessions.
///
/// The runtime is shared instead of owned per session so synchronous callers can open multiple
/// sessions without creating a Tokio worker pool for each one.
///
/// Note that the returned `Handle` alone wouldn't keep the runtime alive; this function keeps it alive
/// by storing it in a static process global.
fn shared_runtime() -> Result<Handle> {
    static RUNTIME: OnceLock<std::result::Result<Runtime, RuntimeInitError>> = OnceLock::new();
    static RUNTIME_THREAD_ID: AtomicUsize = AtomicUsize::new(0);

    match RUNTIME.get_or_init(|| {
        Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name_fn(|| {
                let id = RUNTIME_THREAD_ID.fetch_add(1, Ordering::Relaxed);
                format!("nominal-eip-blocking-{id}")
            })
            .enable_io() // We use TcpStream
            .enable_time() // We use timeout
            .build()
            .map_err(|source| RuntimeInitError(source.to_string()))
    }) {
        Ok(runtime) => Ok(runtime.handle().clone()),
        Err(source) => Err(Error::CreateRuntime {
            source: Box::new(source.clone()),
        }),
    }
}

/// Blocking explicit-messaging EtherNet/IP session for a single target address.
///
/// This wrapper uses a private shared Tokio runtime to drive the crate's async
/// [`crate::ExplicitSession`]. It is intended for synchronous Rust callers. Do not call its methods
/// from inside an async Tokio task; async callers should use [`crate::ExplicitSession`] directly.
pub struct ExplicitSession {
    runtime: Handle,
    session: crate::ExplicitSession,
}

impl ExplicitSession {
    /// Connect to an EtherNet/IP endpoint and register a session.
    pub fn connect(addr: &str) -> Result<Self> {
        let runtime = shared_runtime()?;
        let session = runtime.block_on(crate::ExplicitSession::connect(addr))?;

        Ok(Self { runtime, session })
    }

    /// Connect to an EtherNet/IP endpoint through a backplane route path.
    pub fn connect_with_route_path_slots(addr: &str, slots: &[u8]) -> Result<Self> {
        let runtime = shared_runtime()?;
        let session = runtime.block_on(crate::ExplicitSession::connect_with_route_path_slots(
            addr, slots,
        ))?;

        Ok(Self { runtime, session })
    }

    /// Read a single PLC tag.
    pub fn read_tag(&mut self, tag_name: &str) -> Result<Value> {
        self.runtime.block_on(self.session.read_tag(tag_name))
    }

    /// Read a structured tag and decode it into a caller-owned type.
    pub fn read_tag_struct<T>(&mut self, tag_name: &str) -> Result<T>
    where
        T: TryFrom<StructuredValue>,
        T::Error: std::error::Error + Send + Sync + 'static,
    {
        self.runtime
            .block_on(self.session.read_tag_struct(tag_name))
    }

    /// Read several tags in a single batch request, preserving input order in the returned list.
    ///
    /// See [`crate::ExplicitSession::read_tags`] for the per-tag error semantics. The outer
    /// [`Result`] reports transport-level failures; per-tag failures appear inside the returned
    /// list as [`Err`] entries so partial successes are preserved.
    pub fn read_tags<S>(&mut self, tag_names: &[S]) -> Result<Vec<(String, Result<Value>)>>
    where
        S: AsRef<str>,
    {
        self.runtime.block_on(self.session.read_tags(tag_names))
    }

    /// Write a user-facing [`Value`] to a PLC tag.
    pub fn write_tag(&mut self, tag_name: &str, value: Value) -> Result<()> {
        self.runtime
            .block_on(self.session.write_tag(tag_name, value))
    }

    /// Encode a caller-owned type into a [`StructuredValue`] and write it to a tag.
    pub fn write_tag_struct<T>(&mut self, tag_name: &str, value: T) -> Result<()>
    where
        T: Into<StructuredValue>,
    {
        self.runtime
            .block_on(self.session.write_tag_struct(tag_name, value))
    }

    /// Unregister the explicit EtherNet/IP session.
    pub fn close(self) -> Result<()> {
        self.runtime.block_on(self.session.close())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::sync::{Arc, Mutex};

    use rust_ethernet_ip::PlcValue;

    use crate::mock_client::{MockClient, MockState};

    fn session_with_state(state: Arc<Mutex<MockState>>) -> ExplicitSession {
        let runtime = shared_runtime().expect("runtime should build");
        let session = crate::ExplicitSession::new_for_test(
            "plc.local",
            MockClient::new(state, vec![Ok(PlcValue::Dint(42))], vec![Ok(())], Ok(())),
        );

        ExplicitSession { runtime, session }
    }

    /// Verifies that the blocking wrapper drives read and write calls through the inner async session.
    #[test]
    fn blocking_read_and_write_drive_inner_session() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let mut session = session_with_state(state.clone());

        assert_eq!(session.read_tag("MotorSpeed").unwrap(), Value::Dint(42));
        session.write_tag("Setpoint", Value::Bool(true)).unwrap();

        let locked = state.lock().expect("mock state poisoned");
        assert_eq!(locked.read_calls, vec!["MotorSpeed".to_owned()]);
        assert_eq!(
            locked.write_calls,
            vec![("Setpoint".to_owned(), PlcValue::Bool(true))]
        );
    }

    /// Verifies that closing the blocking wrapper unregisters the inner async session.
    #[test]
    fn blocking_close_unregisters_inner_session() {
        let state = Arc::new(Mutex::new(MockState::default()));
        let session = session_with_state(state.clone());

        session.close().expect("close should succeed");

        assert_eq!(
            state.lock().expect("mock state poisoned").unregister_calls,
            1
        );
    }
}
