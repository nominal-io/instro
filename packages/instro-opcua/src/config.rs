use std::path::PathBuf;

use opcua::client::ClientBuilder;
use opcua::client::ClientEndpoint as NativeEndpoint;
use opcua::client::ClientUserToken as NativeUserToken;
use pyo3::exceptions::PyTypeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::time::Duration;

#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct IdentityToken(pub opcua::client::IdentityToken);

#[pymethods]
impl IdentityToken {
    #[staticmethod]
    fn anonymous() -> Self {
        Self(opcua::client::IdentityToken::Anonymous)
    }

    #[staticmethod]
    fn user_name(username: String, password: String) -> Self {
        Self(opcua::client::IdentityToken::new_user_name(
            username, password,
        ))
    }

    #[staticmethod]
    fn x509(certificate_path: PathBuf, private_key_path: PathBuf) -> PyResult<Self> {
        opcua::client::IdentityToken::new_x509_path(certificate_path, private_key_path)
            .map(Self)
            .map_err(crate::error::native)
    }
}

#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct ClientEndpoint(pub NativeEndpoint);

#[pymethods]
impl ClientEndpoint {
    #[new]
    #[pyo3(signature = (url, *, security_policy="None", security_mode="None", user_token_id="ANONYMOUS"))]
    fn new(url: String, security_policy: &str, security_mode: &str, user_token_id: &str) -> Self {
        Self(NativeEndpoint {
            url,
            security_policy: security_policy.to_owned(),
            security_mode: security_mode.to_owned(),
            user_token_id: user_token_id.to_owned(),
        })
    }
}

#[pyclass(frozen, from_py_object, module = "instro.unstable._opcua")]
#[derive(Clone)]
pub struct ClientUserToken(pub NativeUserToken);

#[pymethods]
impl ClientUserToken {
    #[staticmethod]
    fn user_pass(username: String, password: String) -> Self {
        Self(NativeUserToken::user_pass(username, password))
    }

    #[staticmethod]
    fn x509(username: String, certificate_path: PathBuf, private_key_path: PathBuf) -> Self {
        Self(NativeUserToken::x509(
            username,
            &certificate_path,
            &private_key_path,
        ))
    }
}

macro_rules! native_settings {
    ($($name:ident: $ty:ty),* $(,)?) => {
        pub fn builder(settings: Option<&Bound<'_, PyDict>>) -> PyResult<ClientBuilder> {
            let config_file = match settings {
                Some(settings) => settings.get_item("config_file")?,
                None => None,
            };
            let mut builder = match config_file {
                Some(path) => ClientBuilder::from_config(path.extract::<PathBuf>()?)
                    .map_err(|error| PyValueError::new_err(format!("{error:?}")))?,
                None => ClientBuilder::new(),
            };
            for (name, value) in settings.into_iter().flat_map(|settings| settings.iter()) {
                match name.extract::<String>()?.as_str() {
                    $(stringify!($name) => builder = builder.$name(value.extract::<$ty>()?),)*
                    "config_file" => {}
                    "session_retry_initial" => builder = builder.session_retry_initial(value.extract::<Duration>()?.0),
                    "session_retry_max" => builder = builder.session_retry_max(value.extract::<Duration>()?.0),
                    "keep_alive_interval" => builder = builder.keep_alive_interval(value.extract::<Duration>()?.0),
                    "request_timeout" => builder = builder.request_timeout(value.extract::<Duration>()?.0),
                    "publish_timeout" => builder = builder.publish_timeout(value.extract::<Duration>()?.0),
                    "min_publish_interval" => builder = builder.min_publish_interval(value.extract::<Duration>()?.0),
                    "session_retry_limit" => {
                        let limit = value.extract::<i32>()?;
                        if limit < -1 { return Err(PyValueError::new_err("session_retry_limit must be -1, zero, or positive")); }
                        builder = builder.session_retry_limit(limit);
                    }
                    "endpoint" => {
                        let (id, endpoint) = value.extract::<(String, ClientEndpoint)>()?;
                        builder = builder.endpoint(id, endpoint.0);
                    }
                    "endpoints" => {
                        let endpoints = value.extract::<Vec<(String, ClientEndpoint)>>()?
                            .into_iter().map(|(id, endpoint)| (id, endpoint.0)).collect();
                        builder = builder.endpoints(endpoints);
                    }
                    "user_token" => {
                        let (id, token) = value.extract::<(String, ClientUserToken)>()?;
                        if id == "ANONYMOUS" { return Err(PyValueError::new_err("ANONYMOUS is reserved by upstream")); }
                        builder = builder.user_token(id, token.0);
                    }
                    name => return Err(PyTypeError::new_err(format!("unknown native client setting: {name}"))),
                }
            }
            Ok(builder)
        }
    };
}

native_settings! {
    application_name: String,
    application_uri: String,
    product_uri: String,
    create_sample_keypair: bool,
    certificate_path: PathBuf,
    private_key_path: PathBuf,
    trust_server_certs: bool,
    verify_server_certs: bool,
    pki_dir: PathBuf,
    preferred_locales: Vec<String>,
    default_endpoint: String,
    channel_lifetime: u32,
    max_array_length: usize,
    max_byte_string_length: usize,
    max_chunk_count: usize,
    max_chunk_size: usize,
    max_incoming_chunk_size: usize,
    max_message_size: usize,
    max_string_length: usize,
    max_failed_keep_alive_count: u64,
    ignore_clock_skew: bool,
    recreate_monitored_items_chunk: usize,
    recreate_subscriptions: bool,
    session_name: String,
    session_timeout: u32,
    session_nonce_length: usize,
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<IdentityToken>()?;
    module.add_class::<ClientEndpoint>()?;
    module.add_class::<ClientUserToken>()?;
    Ok(())
}
