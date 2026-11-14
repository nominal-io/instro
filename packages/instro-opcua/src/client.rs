use std::path::PathBuf;
use std::sync::Arc;
use std::sync::mpsc;

use futures_util::StreamExt;
use opcua::client::ClientBuilder;
use opcua::client::Session;
use opcua::client::SessionDropGuard;
use opcua::client::SessionEventLoop;
use opcua::client::SessionPollResult;
use opcua::client::transport::TcpConnector;
use opcua::types as ua;
use pyo3::exceptions::PyBaseException;
use pyo3::exceptions::PyRuntimeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use tokio::sync::RwLock;
use tokio::sync::oneshot;
use tokio::sync::watch;

use crate::config::DurationInput;
use crate::config::IdentityToken;
use crate::config::NativeInteger;
use crate::config::SecurityModeInput;
use crate::config::SecurityPolicyInput;
use crate::config::{self};
use crate::error;
use crate::util;
use crate::values::EndpointDescription;
use crate::values::UaType;

#[derive(Clone)]
pub(crate) enum ClientState {
    Recovering,
    Connected,
    Finished(Result<ua::StatusCode, Arc<tokio::task::JoinError>>),
}

impl ClientState {
    pub(crate) fn finished_result(&self) -> Option<PyResult<()>> {
        match self {
            Self::Finished(Ok(code)) if code.is_good() => Some(Ok(())),
            Self::Finished(Ok(code)) => Some(Err(error::status(*code, "native session ended"))),
            Self::Finished(Err(error)) => Some(Err(PyRuntimeError::new_err(error.to_string()))),
            _ => None,
        }
    }
}

/// A shared synchronous OPC UA session, created with ``connect()``.
///
/// Nodes and subscriptions retain this client. Explicit disconnection affects
/// every handle; dropping the original Python variable alone does not.
/// Use a context manager to disconnect when leaving a scope.
///
/// Examples:
///     >>> from instro.unstable.opcua import OpcUaClient
///     >>> with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
///     ...     print(client.objects.node_id)
#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub(crate) struct OpcUaClient {
    session: Arc<RwLock<Option<SessionDropGuard>>>,
    state: watch::Receiver<ClientState>,
}

impl OpcUaClient {
    fn from_native(session: Arc<Session>, driver: SessionEventLoop<TcpConnector>) -> Self {
        let guard = session.close_on_drop();

        let (state_tx, state) = watch::channel(ClientState::Recovering);
        let progress = state_tx.clone();

        let task = tokio::spawn(async move {
            let events = driver.enter();
            tokio::pin!(events);

            while let Some(event) = events.next().await {
                match event {
                    Err(code) => return code,

                    Ok(SessionPollResult::Reconnected(_)) => {
                        progress.send_replace(ClientState::Connected);
                    }

                    Ok(
                        SessionPollResult::BeginConnect
                        | SessionPollResult::ConnectionLost(_)
                        | SessionPollResult::ReconnectFailed(_),
                    ) => {
                        progress.send_replace(ClientState::Recovering);
                    }

                    _ => (),
                }
            }

            ua::StatusCode::Good
        });

        tokio::spawn(async move {
            state_tx.send_replace(ClientState::Finished(task.await.map_err(Arc::new)));
        });

        Self {
            session: Arc::new(RwLock::new(Some(guard))),
            state,
        }
    }

    async fn activate(self, cancelled: &mut oneshot::Receiver<()>) -> PyResult<Option<Self>> {
        let activation = self.with_session(async |session| {
            if session.wait_for_connection().await {
                Ok(())
            } else {
                Err(disconnected())
            }
        });

        let mut completion = self.state.clone();

        let activated = tokio::select! {
            biased;
            _ = cancelled => None,

            result = wait_for_completion(&mut completion) =>
                Some(result.and_then(|()| Err(disconnected()))),

            result = activation => Some(result),
        };

        match activated {
            Some(Ok(())) => Ok(Some(self)),

            Some(Err(error)) => {
                let _ = self.disconnect_async().await;
                Err(error)
            }

            None => {
                let _ = self.disconnect_async().await;
                Ok(None)
            }
        }
    }

    pub(crate) async fn with_session<T>(
        &self,
        operation: impl AsyncFnOnce(&Session) -> PyResult<T>,
    ) -> PyResult<T> {
        let guard = self.session.read().await;
        let session = guard.as_ref().ok_or_else(disconnected)?;

        if let Some(result) = self.state.borrow().finished_result() {
            result?;
            return Err(disconnected());
        }

        operation(session).await
    }

    pub(crate) fn same_client(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.session, &other.session)
    }

    pub(crate) fn native_state(&self) -> watch::Receiver<ClientState> {
        self.state.clone()
    }

    pub(crate) async fn disconnect_async(&self) -> PyResult<()> {
        let Some(session) = self.session.write().await.take() else {
            return Ok(());
        };

        let mut state = self.state.clone();
        let healthy = matches!(*state.borrow_and_update(), ClientState::Connected);

        let result = if healthy {
            let disconnect = session.disconnect();
            tokio::pin!(disconnect);

            loop {
                tokio::select! {
                    biased;
                    result = &mut disconnect => break result.map_err(error::native),

                    changed = state.changed() => {
                        changed
                            .map_err(|_| PyRuntimeError::new_err("native session supervisor ended"))?;

                        if !matches!(*state.borrow_and_update(), ClientState::Connected) {
                            break Ok(());
                        }
                    }
                }
            }
        } else {
            Ok(())
        };

        drop(session);
        let completion = wait_for_completion(&mut state).await;

        result.and(completion)
    }
}

fn validate_retry_timing(
    initial: std::time::Duration,
    maximum: std::time::Duration,
    retry_limit: i32,
) -> PyResult<()> {
    let retries = if retry_limit == -1 {
        u32::MAX
    } else {
        retry_limit as u32
    };

    let now = std::time::Instant::now();
    let mut delay = initial;

    for _ in 0..retries {
        // Native backoff doubles before clamping and then adds the yielded delay to Instant.
        let doubled = delay.checked_mul(2).ok_or_else(|| {
            PyValueError::new_err("session retry durations exceed native backoff arithmetic")
        })?;

        now.checked_add(delay).ok_or_else(|| {
            PyValueError::new_err("session retry durations exceed the native clock range")
        })?;

        let next = maximum.min(doubled);
        if next == delay {
            break;
        }

        delay = next;
    }

    Ok(())
}

fn disconnected() -> PyErr {
    error::status(
        ua::StatusCode::BadServerNotConnected,
        "client is disconnected",
    )
}

async fn wait_for_completion(state: &mut watch::Receiver<ClientState>) -> PyResult<()> {
    loop {
        if let Some(result) = state.borrow_and_update().finished_result() {
            return result;
        }

        state.changed().await.map_err(|_| {
            PyRuntimeError::new_err("native session supervisor ended without a result")
        })?;
    }
}

#[pymethods]
impl OpcUaClient {
    /// Connect to an endpoint using native OPC UA connection and retry settings.
    ///
    /// Duration parameters accept ``Duration``, ``datetime.timedelta``, or finite
    /// nonnegative numeric seconds. Integer millisecond parameters retain their
    /// native units. All settings after ``endpoint_url`` are keyword-only.
    /// Waiting releases the GIL and checks Python signals. An interrupted setup
    /// initiates cleanup without waiting for it to finish.
    ///
    /// Args:
    ///     endpoint_url (str): OPC UA server endpoint URL.
    ///     identity (IdentityToken | None): User identity; ``None`` is anonymous.
    ///     security_policy (SecurityPolicy | str): Policy value, canonical name,
    ///         supported AES alias, or full policy URI. Defaults to ``None`` policy.
    ///     security_mode (MessageSecurityMode | str): ``None``, ``Sign``, or
    ///         ``SignAndEncrypt``; names are case-insensitive. Defaults to ``None`` mode.
    ///     application_name (str): Application display name. Defaults to
    ///         ``Nominal OPC UA Client``.
    ///     application_uri (str): Application URI. Defaults to ``urn:nominal:opcua-client``.
    ///     product_uri (str): Product URI. Defaults to an empty string.
    ///     create_sample_keypair (bool): Generate a sample application key pair
    ///         when native provisioning requires it. Defaults to ``False``.
    ///     certificate_path (str | os.PathLike[str] | None): Application certificate
    ///         path relative to the PKI directory; supply with ``private_key_path``. Defaults to ``None``.
    ///     private_key_path (str | os.PathLike[str] | None): Application private key
    ///         path relative to the PKI directory; supply with ``certificate_path``. Defaults to ``None``.
    ///     trust_server_certs (bool): Automatically place server certificates in the
    ///         native trusted store. Defaults to ``False``.
    ///     verify_server_certs (bool): Verify hostname, application URI, and validity dates.
    ///         Trust and key-length checks still apply when false. Defaults to ``True``.
    ///     pki_dir (str | os.PathLike[str] | None): Native PKI directory.
    ///         Defaults to the native PKI location when omitted.
    ///     preferred_locales (list[str]): Preferred locale IDs, in order. Defaults to ``[]``.
    ///     channel_lifetime (int): Requested secure channel lifetime in milliseconds.
    ///         Defaults to ``60000``.
    ///     session_retry_limit (int): Native retry limit: ``-1`` for unlimited,
    ///         ``0`` for none, or a positive count. Defaults to ``10``.
    ///     session_retry_initial (Duration | datetime.timedelta | int | float):
    ///         Initial native reconnect backoff. Defaults to 1 second.
    ///     session_retry_max (Duration | datetime.timedelta | int | float):
    ///         Maximum native reconnect backoff. Defaults to 30 seconds.
    ///     keep_alive_interval (Duration | datetime.timedelta | int | float):
    ///         Positive session keep-alive interval. Defaults to 10 seconds.
    ///     max_array_length (int): Maximum decoded array length; zero permits no
    ///         array elements. Defaults to ``1000``.
    ///     max_byte_string_length (int): Maximum decoded byte-string bytes; zero
    ///         permits no bytes. Defaults to ``65535``.
    ///     max_chunk_count (int): Maximum chunks per message; zero means unlimited.
    ///         Defaults to ``5``.
    ///     max_chunk_size (int): Maximum outgoing chunk bytes. Defaults to ``65535``.
    ///     max_incoming_chunk_size (int): Maximum incoming chunk bytes. Defaults to ``65535``.
    ///     max_message_size (int): Maximum outgoing message bytes; zero means
    ///         unlimited. Defaults to ``327675``.
    ///     max_string_length (int): Maximum decoded string bytes; zero permits no
    ///         string content. Defaults to ``65535``.
    ///     max_failed_keep_alive_count (int): Failed keep-alive limit before native
    ///         closure; zero disables this limit. Defaults to ``0``.
    ///     request_timeout (Duration | datetime.timedelta | int | float): Ordinary
    ///         native request timeout. Defaults to 60 seconds.
    ///     publish_timeout (Duration | datetime.timedelta | int | float): Native
    ///         publish request timeout. Defaults to 60 seconds.
    ///     min_publish_interval (Duration | datetime.timedelta | int | float):
    ///         Client minimum publish interval. Defaults to 0.1 seconds.
    ///     ignore_clock_skew (bool): Ignore clock skew in native transport handling.
    ///         Defaults to ``False``.
    ///     recreate_monitored_items_chunk (int): Maximum monitored items per
    ///         restoration request. Defaults to ``1000``.
    ///     recreate_subscriptions (bool): Let native recovery transfer or recreate
    ///         subscriptions. Defaults to ``True``.
    ///     session_name (str): Requested session name. Defaults to ``Rust OPC UA Client``.
    ///     session_timeout (int): Requested session timeout in milliseconds.
    ///         Defaults to ``60000``.
    ///     session_nonce_length (int): CreateSession nonce length in bytes. Defaults to ``32``.
    ///
    /// Returns:
    ///     OpcUaClient: An activated shared client.
    ///
    /// Raises:
    ///     TypeError: A keyword or argument type is unsupported.
    ///     ValueError: A setting is invalid or exceeds native bounds.
    ///     OpcUaError: Native connection or activation fails.
    ///     KeyboardInterrupt: Python interrupts the connection wait.
    #[staticmethod]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature =  (
        endpoint_url,
        *,
        identity = None,
        security_policy = SecurityPolicyInput::NONE,
        security_mode = SecurityModeInput::NONE,
        application_name = config::APPLICATION_NAME,
        application_uri = config::APPLICATION_URI,
        product_uri = "",
        create_sample_keypair = false,
        certificate_path = None,
        private_key_path = None,
        trust_server_certs = false,
        verify_server_certs = true,
        pki_dir = None,
        preferred_locales = Vec::new(),
        channel_lifetime = NativeInteger(60_000),
        session_retry_limit = NativeInteger(10),
        session_retry_initial = DurationInput::from_secs(1),
        session_retry_max = DurationInput::from_secs(30),
        keep_alive_interval = DurationInput::from_secs(10),
        max_array_length = NativeInteger(1000),
        max_byte_string_length = NativeInteger(65535),
        max_chunk_count = NativeInteger(5),
        max_chunk_size = NativeInteger(65535),
        max_incoming_chunk_size = NativeInteger(65535),
        max_message_size = NativeInteger(327675),
        max_string_length = NativeInteger(65535),
        max_failed_keep_alive_count = NativeInteger(0),
        request_timeout = DurationInput::from_secs(60),
        publish_timeout = DurationInput::from_secs(60),
        min_publish_interval = DurationInput::from_millis(100),
        ignore_clock_skew = false,
        recreate_monitored_items_chunk = NativeInteger(1000),
        recreate_subscriptions = true,
        session_name = "Rust OPC UA Client",
        session_timeout = NativeInteger(60_000),
        session_nonce_length = NativeInteger(32)
    ))]
    fn connect(
        py: Python<'_>,
        endpoint_url: String,
        identity: Option<IdentityToken>,
        security_policy: SecurityPolicyInput,
        security_mode: SecurityModeInput,
        application_name: &str,
        application_uri: &str,
        product_uri: &str,
        create_sample_keypair: bool,
        certificate_path: Option<PathBuf>,
        private_key_path: Option<PathBuf>,
        trust_server_certs: bool,
        verify_server_certs: bool,
        pki_dir: Option<PathBuf>,
        preferred_locales: Vec<String>,
        channel_lifetime: NativeInteger<u32>,
        session_retry_limit: NativeInteger<i32>,
        session_retry_initial: DurationInput,
        session_retry_max: DurationInput,
        keep_alive_interval: DurationInput,
        max_array_length: NativeInteger<usize>,
        max_byte_string_length: NativeInteger<usize>,
        max_chunk_count: NativeInteger<usize>,
        max_chunk_size: NativeInteger<usize>,
        max_incoming_chunk_size: NativeInteger<usize>,
        max_message_size: NativeInteger<usize>,
        max_string_length: NativeInteger<usize>,
        max_failed_keep_alive_count: NativeInteger<u64>,
        request_timeout: DurationInput,
        publish_timeout: DurationInput,
        min_publish_interval: DurationInput,
        ignore_clock_skew: bool,
        recreate_monitored_items_chunk: NativeInteger<usize>,
        recreate_subscriptions: bool,
        session_name: &str,
        session_timeout: NativeInteger<u32>,
        session_nonce_length: NativeInteger<usize>,
    ) -> PyResult<Self> {
        config::validate_certificate_paths(
            certificate_path.as_deref(),
            private_key_path.as_deref(),
        )?;

        let retry_limit = config::validate_retry_limit(session_retry_limit.0)?;
        validate_retry_timing(
            session_retry_initial.into_native(),
            session_retry_max.into_native(),
            retry_limit,
        )?;

        if keep_alive_interval.into_native().is_zero() {
            return Err(PyValueError::new_err(
                "keep_alive_interval must be positive",
            ));
        }

        let now = std::time::Instant::now();
        let timed_settings = [
            ("keep_alive_interval", keep_alive_interval),
            ("request_timeout", request_timeout),
            ("publish_timeout", publish_timeout),
            ("min_publish_interval", min_publish_interval),
        ];

        for (name, duration) in timed_settings {
            if now.checked_add(duration.into_native()).is_none() {
                return Err(PyValueError::new_err(format!(
                    "{name} exceeds the native clock range"
                )));
            }
        }

        let publish_interval_exceeds_clock = now
            .checked_sub(min_publish_interval.into_native())
            .is_none();

        if publish_interval_exceeds_clock {
            return Err(PyValueError::new_err(
                "min_publish_interval exceeds the native clock range",
            ));
        }

        let mut builder = ClientBuilder::new()
            .application_name(application_name)
            .application_uri(application_uri)
            .product_uri(product_uri)
            .create_sample_keypair(create_sample_keypair)
            .trust_server_certs(trust_server_certs)
            .verify_server_certs(verify_server_certs)
            .preferred_locales(preferred_locales)
            .channel_lifetime(channel_lifetime.0)
            .session_retry_limit(retry_limit)
            .session_retry_initial(session_retry_initial.into_native())
            .session_retry_max(session_retry_max.into_native())
            .keep_alive_interval(keep_alive_interval.into_native())
            .max_array_length(max_array_length.0)
            .max_byte_string_length(max_byte_string_length.0)
            .max_chunk_count(max_chunk_count.0)
            .max_chunk_size(max_chunk_size.0)
            .max_incoming_chunk_size(max_incoming_chunk_size.0)
            .max_message_size(max_message_size.0)
            .max_string_length(max_string_length.0)
            .max_failed_keep_alive_count(max_failed_keep_alive_count.0)
            .request_timeout(request_timeout.into_native())
            .publish_timeout(publish_timeout.into_native())
            .min_publish_interval(min_publish_interval.into_native())
            .ignore_clock_skew(ignore_clock_skew)
            .recreate_monitored_items_chunk(recreate_monitored_items_chunk.0)
            .recreate_subscriptions(recreate_subscriptions)
            .session_name(session_name)
            .session_timeout(session_timeout.0)
            .session_nonce_length(session_nonce_length.0);

        if let Some(path) = pki_dir {
            builder = builder.pki_dir(path);
        }

        if let Some(path) = certificate_path {
            builder = builder.certificate_path(path);
        }

        if let Some(path) = private_key_path {
            builder = builder.private_key_path(path);
        }

        let mut factory = builder
            .client()
            .map_err(|errors| PyValueError::new_err(errors.join("; ")))?;

        let endpoint = ua::EndpointDescription {
            endpoint_url: endpoint_url.into(),
            security_policy_uri: security_policy.into_native().to_uri().into(),
            security_mode: security_mode.into_native(),
            ..Default::default()
        };

        let identity = identity
            .map(|value| value.as_ua_ref().clone())
            .unwrap_or(opcua::client::IdentityToken::Anonymous);

        let runtime = crate::shared_rt::get(py)?;
        let (cancel, mut cancelled) = oneshot::channel::<()>();
        let (result_tx, result_rx) = mpsc::sync_channel(1);

        runtime.spawn(async move {
            let resource = tokio::select! {
                biased;
                _ = &mut cancelled => return,
                result = factory.connect_to_matching_endpoint(endpoint, identity) => result,
            };

            let result = match resource {
                Err(error) => Err(error::native(error)),

                Ok((session, driver)) => {
                    let activated = Self::from_native(session, driver)
                        .activate(&mut cancelled)
                        .await;

                    match activated {
                        Ok(Some(client)) => Ok(client),
                        Ok(None) => return,
                        Err(error) => Err(error),
                    }
                }
            };

            let _ = result_tx.send(result);
        });

        let result = util::receive(py, result_rx);
        drop(cancel);

        result?
    }

    /// Disconnect the shared session and wait for owned cleanup with the GIL released.
    ///
    /// Admitted operations finish before the session is removed. This caller waits
    /// if it takes cleanup ownership; repeated callers return without a shared
    /// completion barrier. During recovery, native termination may skip graceful
    /// protocol shutdown. This wait is not cancelled by Python signals.
    ///
    /// Raises:
    ///     OpcUaError: Native shutdown reports a failure.
    fn disconnect(&self, py: Python<'_>) -> PyResult<()> {
        let client = self.clone();
        util::wait(py, async move { client.disconnect_async().await })
    }

    /// Enable native reconnect attempts for the existing session.
    ///
    /// This does not restart a native event loop that has already finished.
    fn enable_reconnects(&self, py: Python<'_>) -> PyResult<()> {
        let client = self.clone();
        util::wait(py, async move {
            client
                .with_session(async |session| {
                    session.enable_reconnects();
                    Ok(())
                })
                .await
        })
    }

    /// Disable native reconnect attempts for the existing session.
    fn disable_reconnects(&self, py: Python<'_>) -> PyResult<()> {
        let client = self.clone();
        util::wait(py, async move {
            client
                .with_session(async |session| {
                    session.disable_reconnects();
                    Ok(())
                })
                .await
        })
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(
        &self,
        py: Python<'_>,
        _kind: &Bound<'_, PyAny>,
        value: Option<&Bound<'_, PyBaseException>>,
        _traceback: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        match (value, self.disconnect(py)) {
            (Some(body), Err(cleanup)) => {
                PyErr::from_value(body.clone().into_any()).set_context(py, Some(cleanup));
                Ok(())
            }

            (_, result) => result,
        }
    }
}

/// Discover endpoint security, identity, and application metadata.
///
/// Waiting releases the GIL and checks Python signals.
///
/// Args:
///     endpoint_url (str): OPC UA discovery endpoint URL.
///
/// Returns:
///     list[EndpointDescription]: Endpoints reported by the server, in response order.
///
/// Raises:
///     OpcUaError: Native endpoint discovery fails.
#[pyfunction]
fn get_endpoints(py: Python<'_>, endpoint_url: String) -> PyResult<Vec<EndpointDescription>> {
    let factory = ClientBuilder::new()
        .application_name(config::APPLICATION_NAME)
        .application_uri(config::APPLICATION_URI)
        .client()
        .map_err(|errors| PyValueError::new_err(errors.join("; ")))?;

    let runtime = crate::shared_rt::get(py)?;
    let (cancel, mut cancelled) = oneshot::channel::<()>();
    let (tx, rx) = mpsc::sync_channel(1);

    runtime.spawn(async move {
        let result = tokio::select! {
            biased;
            _ = &mut cancelled => return,
            result = factory.get_server_endpoints_from_url(endpoint_url) => result,
        };

        let endpoints = result
            .map(|values| {
                values
                    .into_iter()
                    .map(<EndpointDescription as UaType>::new)
                    .collect()
            })
            .map_err(error::native);

        let _ = tx.send(endpoints);
    });

    let result = util::receive(py, rx);
    drop(cancel);

    result?
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<OpcUaClient>()?;
    module.add_function(wrap_pyfunction!(get_endpoints, module)?)
}

#[cfg(test)]
pub(crate) mod tests;
