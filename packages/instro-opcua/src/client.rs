use std::sync::Arc;
use std::time::Duration;

use anyhow::Context as _;
use instro_opcua::browse::Browse as _;
use instro_opcua::client::OpcUaClient as _Client;
use instro_opcua::client::OpcUaClientBuilder as _ClientBuilder;
use instro_opcua::types::OpcUaBrowsePath as _BrowsePath;
use instro_opcua::types::OpcUaNode as _Node;
use instro_opcua::types::OpcUaNodeId as _NodeId;
use instro_opcua::types::OpcUaSecurityMode as _SecurityMode;
use instro_opcua::types::OpcUaSecurityPolicy as _SecurityPolicy;
use instro_opcua::types::OpcUaUserToken as _UserToken;
use instro_utils::py_bail;
use instro_utils::pyo3::ResultExt as _;
use open62541::Certificate;
use open62541::Password;
use open62541::PrivateKey;
use pyo3::exceptions::PyResourceWarning;
use pyo3::exceptions::PyRuntimeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyAny;
use pyo3::types::PyType;
use tokio::runtime;
use tokio::runtime::Runtime;

use crate::errors::OpcUaError;
use crate::node::OpcUaNode;

#[pyclass(module = "instro.unstable._opcua")]
pub(crate) struct OpcUaClient {
    endpoint_url: String,
    runtime: Runtime,
    client: Option<Arc<_Client>>,
}

impl OpcUaClient {
    fn active_client(&self) -> PyResult<Arc<_Client>> {
        self.client
            .clone()
            .ok_or_else(|| OpcUaError::new_err("OPC UA client is closed"))
    }

    fn ensure_open(&self) -> PyResult<()> {
        if self.client.is_some() {
            Ok(())
        } else {
            Err(PyRuntimeError::new_err("OPC UA client is closed"))
        }
    }
}

impl Drop for OpcUaClient {
    fn drop(&mut self) {
        if self.client.is_none() {
            return;
        }

        Python::try_attach(|py| {
            if let Err(error) = PyErr::warn(
                py,
                &py.get_type::<PyResourceWarning>(),
                c"unclosed OpcUaClient was garbage collected; call close() or use a context manager",
                1,
            ) {
                error.write_unraisable(py, None);
            }
        });
    }
}

#[pymethods]
impl OpcUaClient {
    #[classmethod]
    #[pyo3(signature = (
        endpoint_url,
        *,
        security_mode=None,
        security_policy=None,
        username=None,
        password=None,
        user_certificate=None,
        user_token_policy_id=None,
        certificate=None,
        private_key=None,
        private_key_password=None,
        generate_self_signed_pki=None,
        trust_server_certificates=None,
        timeout=None,
        secure_channel_lifetime=None,
        requested_session_timeout=None,
        connectivity_check_interval=None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn connect(
        _cls: &Bound<'_, PyType>,
        py: Python<'_>,
        endpoint_url: String,
        security_mode: Option<String>,
        security_policy: Option<String>,
        username: Option<String>,
        password: Option<String>,
        user_certificate: Option<Vec<u8>>,
        user_token_policy_id: Option<String>,
        certificate: Option<Vec<u8>>,
        private_key: Option<Vec<u8>>,
        private_key_password: Option<String>,
        generate_self_signed_pki: Option<bool>,
        trust_server_certificates: Option<bool>,
        timeout: Option<f64>,
        secure_channel_lifetime: Option<f64>,
        requested_session_timeout: Option<f64>,
        connectivity_check_interval: Option<f64>,
    ) -> PyResult<Self> {
        let mut builder = _ClientBuilder::new();

        if let Some(mode) = security_mode {
            builder = builder.security_mode(parse_security_mode(&mode)?);
        }
        if let Some(policy) = security_policy {
            builder = builder.security_policy(parse_security_policy(&policy)?);
        }

        builder = apply_identity(
            builder,
            username,
            password,
            user_certificate,
            user_token_policy_id,
        )?;

        builder = apply_pki(
            builder,
            certificate,
            private_key,
            private_key_password,
            generate_self_signed_pki,
        )?;

        if let Some(value) = trust_server_certificates {
            builder = builder.trust_server_certs(value);
        }

        if let Some(value) = timeout {
            builder = builder.timeout(try_parse_duration("timeout", value)?);
        }

        if let Some(value) = secure_channel_lifetime {
            builder = builder
                .secure_channel_lifetime(try_parse_duration("secure_channel_lifetime", value)?);
        }

        if let Some(value) = requested_session_timeout {
            builder = builder
                .requested_session_timeout(try_parse_duration("requested_session_timeout", value)?);
        }

        if let Some(value) = connectivity_check_interval {
            builder = builder.connectivity_check_interval(Some(try_parse_duration(
                "connectivity_check_interval",
                value,
            )?));
        }

        let target = endpoint_url.clone();

        let client = py
            .detach(move || builder.connect(&target))
            .context("failed to connect to OPC UA server")
            .into_py::<OpcUaError>()?;

        let runtime = runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .context("failed to build runtime")
            .into_py::<OpcUaError>()?;

        Ok(Self {
            endpoint_url,
            runtime,
            client: Some(client),
        })
    }

    #[getter]
    fn endpoint_url(&self) -> &str {
        &self.endpoint_url
    }

    #[getter]
    fn closed(&self) -> bool {
        self.client.is_none()
    }

    fn get_node(&self, py: Python<'_>, query: &str) -> PyResult<OpcUaNode> {
        let client = self.active_client()?;
        let query = parse_node_query(query)?;
        let node = py
            .detach(|| {
                self.runtime.block_on(async {
                    let (node_id, browse_path) = match query {
                        NodeQuery::NodeId(node_id) => (node_id, _BrowsePath::default()),
                        NodeQuery::BrowsePath(browse_path) => {
                            let node_id = client.resolve_browse_path(&browse_path).await?;
                            (node_id, browse_path)
                        }
                    };
                    let (browse_name, display_name, node_class) =
                        client.read_node_metadata(&node_id).await?;

                    Ok::<_, anyhow::Error>(_Node {
                        node_id,
                        browse_name: browse_name.name,
                        display_name,
                        node_class,
                        browse_path,
                        children: Vec::new(),
                    })
                })
            })
            .context("failed to get node")
            .into_py::<OpcUaError>()?;

        Ok(node.into())
    }

    fn resolve_browse_path(&self, py: Python<'_>, browse_path: &str) -> PyResult<String> {
        let client = self.active_client()?;
        let browse_path = browse_path
            .parse::<_BrowsePath>()
            .context("failed to parse browse path")
            .into_py::<PyValueError>()?;
        let node_id = py
            .detach(|| {
                self.runtime
                    .block_on(client.resolve_browse_path(&browse_path))
            })
            .context("failed to resolve browse path")
            .into_py::<OpcUaError>()?;

        Ok(node_id.to_string())
    }

    fn close(&mut self, py: Python<'_>) -> PyResult<()> {
        if let Some(client) = self.client.take() {
            py.detach(|| self.runtime.block_on(client.disconnect()))
                .context("failed to disconnect from OPC UA server")
                .into_py::<OpcUaError>()?;
        }

        Ok(())
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyResult<PyRef<'_, Self>> {
        slf.ensure_open()?;
        Ok(slf)
    }

    #[pyo3(signature = (_exc_type=None, _exc=None, _tb=None))]
    fn __exit__(
        &mut self,
        py: Python<'_>,
        _exc_type: Option<&Bound<'_, PyAny>>,
        _exc: Option<&Bound<'_, PyAny>>,
        _tb: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<bool> {
        self.close(py)?;
        Ok(false)
    }

    fn __repr__(&self) -> String {
        format!(
            "OpcUaClient(endpoint_url={:?}, closed={})",
            self.endpoint_url,
            self.client.is_none()
        )
    }
}

enum NodeQuery {
    NodeId(_NodeId),
    BrowsePath(_BrowsePath),
}

fn parse_node_query(value: &str) -> PyResult<NodeQuery> {
    if value.starts_with('/') {
        value
            .parse()
            .map(NodeQuery::BrowsePath)
            .context("failed to parse browse path")
            .into_py::<PyValueError>()
    } else {
        value
            .parse()
            .map(NodeQuery::NodeId)
            .context("failed to parse node ID")
            .into_py::<PyValueError>()
    }
}

fn apply_certificate_identity(
    cert_bytes: Vec<u8>,
    policy_id: String,
) -> anyhow::Result<_UserToken> {
    _UserToken::certificate(cert_bytes, policy_id)
        .context("failed to create certificate user token")
}

fn apply_basic_identity(
    username: String,
    password: String,
    policy_id: String,
) -> anyhow::Result<_UserToken> {
    _UserToken::basic(username, password, policy_id).context("failed to create basic user token")
}

fn apply_anonymous_identity(policy_id: String) -> anyhow::Result<_UserToken> {
    _UserToken::anonymous(policy_id).context("failed to create anonymous user token")
}

fn apply_identity(
    builder: _ClientBuilder,
    username: Option<String>,
    password: Option<String>,
    user_certificate: Option<Vec<u8>>,
    policy_id: Option<String>,
) -> PyResult<_ClientBuilder> {
    if user_certificate.is_some() && (username.is_some() || password.is_some()) {
        py_bail!(
            PyValueError,
            "user_certificate cannot be combined with username or password"
        );
    }

    let (user, pass, cert, policy) = match (username, password, user_certificate, policy_id) {
        (None, None, None, None) => return Ok(builder),
        (user, pass, cert, policy) => (user, pass, cert, policy.unwrap_or_default()),
    };

    let token = match (user, pass, cert) {
        (None, None, None) => apply_anonymous_identity(policy),
        (Some(user), Some(pass), None) => apply_basic_identity(user, pass, policy),
        (None, None, Some(cert)) => apply_certificate_identity(cert, policy),
        _ => py_bail!(
            PyValueError,
            "can't combine basic or certificate identity options"
        ),
    }
    .into_py::<OpcUaError>()?;

    Ok(builder.user_identity_token(token))
}

fn apply_pki(
    builder: _ClientBuilder,
    certificate: Option<Vec<u8>>,
    private_key: Option<Vec<u8>>,
    private_key_password: Option<String>,
    generate_self_signed: Option<bool>,
) -> PyResult<_ClientBuilder> {
    if let Some(true) = generate_self_signed {
        if certificate.is_some() || private_key.is_some() || private_key_password.is_some() {
            py_bail!(
                PyValueError,
                "generated PKI cannot be combined with provided PKI"
            );
        }

        return Ok(builder.generate_self_signed_pki());
    }

    match (certificate, private_key, private_key_password) {
        (None, None, None) => Ok(builder),
        (Some(certificate), Some(private_key), Some(password)) => Ok(builder
            .use_pki_with_password(
                Certificate::from_bytes(&certificate),
                PrivateKey::from_bytes(&private_key),
                Password::from(password),
            )),
        (Some(certificate), Some(private_key), None) => Ok(builder.use_pki(
            Certificate::from_bytes(&certificate),
            PrivateKey::from_bytes(&private_key),
        )),
        (None, None, Some(_)) => py_bail!(
            PyValueError,
            "private_key_password requires certificate and private_key"
        ),
        _ => py_bail!(
            PyValueError,
            "certificate and private_key must be provided together"
        ),
    }
}

fn parse_security_mode(value: &str) -> PyResult<_SecurityMode> {
    match value {
        "none" => Ok(_SecurityMode::None),
        "sign" => Ok(_SecurityMode::Sign),
        "sign_and_encrypt" => Ok(_SecurityMode::SignAndEncrypt),
        _ => py_bail!(PyValueError, format!("unsupported security_mode: {value}")),
    }
}

fn parse_security_policy(value: &str) -> PyResult<_SecurityPolicy> {
    match value {
        "none" => Ok(_SecurityPolicy::None),
        "basic128_rsa15" => Ok(_SecurityPolicy::Basic128Rsa15),
        "basic256" => Ok(_SecurityPolicy::Basic256),
        "basic256_sha256" => Ok(_SecurityPolicy::Basic256Sha256),
        "aes128_sha256_rsa_oaep" => Ok(_SecurityPolicy::Aes128Sha256RsaOaep),
        "aes256_sha256_rsa_pss" => Ok(_SecurityPolicy::Aes256Sha256RsaPss),
        _ => py_bail!(
            PyValueError,
            format!("unsupported security_policy: {value}")
        ),
    }
}

fn try_parse_duration(name: &str, seconds: f64) -> PyResult<Duration> {
    Duration::try_from_secs_f64(seconds)
        .context(format!("{name} must be finite and non-negative"))
        .into_py::<PyValueError>()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_identity_combinations_are_rejected() {
        assert!(
            apply_identity(_ClientBuilder::new(), Some("user".into()), None, None, None,).is_err()
        );
        assert!(
            apply_identity(
                _ClientBuilder::new(),
                None,
                Some("password".into()),
                None,
                None,
            )
            .is_err()
        );
        assert!(
            apply_identity(
                _ClientBuilder::new(),
                Some("user".into()),
                Some("password".into()),
                Some(vec![1, 2, 3]),
                None,
            )
            .is_err()
        );
    }

    #[test]
    fn valid_identity_combinations_are_accepted() {
        assert!(
            apply_identity(
                _ClientBuilder::new(),
                Some("user".into()),
                Some("password".into()),
                None,
                Some("username-policy".into()),
            )
            .is_ok()
        );
        assert!(
            apply_identity(
                _ClientBuilder::new(),
                None,
                None,
                Some(vec![1, 2, 3]),
                Some("certificate-policy".into()),
            )
            .is_ok()
        );
        assert!(
            apply_identity(
                _ClientBuilder::new(),
                None,
                None,
                None,
                Some("anonymous-policy".into()),
            )
            .is_ok()
        );
    }

    #[test]
    fn invalid_pki_combinations_are_rejected() {
        assert!(
            apply_pki(
                _ClientBuilder::new(),
                None,
                None,
                Some("secret".into()),
                None,
            )
            .is_err()
        );
        assert!(apply_pki(_ClientBuilder::new(), Some(vec![1]), None, None, None,).is_err());
        assert!(apply_pki(_ClientBuilder::new(), None, Some(vec![2]), None, None,).is_err());
        assert!(
            apply_pki(
                _ClientBuilder::new(),
                Some(vec![1]),
                Some(vec![2]),
                None,
                Some(true),
            )
            .is_err()
        );
    }

    #[test]
    fn valid_pki_combinations_are_accepted() {
        assert!(
            apply_pki(
                _ClientBuilder::new(),
                Some(vec![1]),
                Some(vec![2]),
                None,
                None,
            )
            .is_ok()
        );
        assert!(
            apply_pki(
                _ClientBuilder::new(),
                Some(vec![1]),
                Some(vec![2]),
                Some("secret".into()),
                None,
            )
            .is_ok()
        );
        assert!(apply_pki(_ClientBuilder::new(), None, None, None, Some(true)).is_ok());
    }

    #[test]
    fn security_modes_parse() {
        assert_eq!(parse_security_mode("none").unwrap(), _SecurityMode::None);
        assert_eq!(parse_security_mode("sign").unwrap(), _SecurityMode::Sign);
        assert_eq!(
            parse_security_mode("sign_and_encrypt").unwrap(),
            _SecurityMode::SignAndEncrypt
        );
        assert!(parse_security_mode("invalid").is_err());
    }

    #[test]
    fn security_policies_parse() {
        let cases = [
            ("none", _SecurityPolicy::None),
            ("basic128_rsa15", _SecurityPolicy::Basic128Rsa15),
            ("basic256", _SecurityPolicy::Basic256),
            ("basic256_sha256", _SecurityPolicy::Basic256Sha256),
            (
                "aes128_sha256_rsa_oaep",
                _SecurityPolicy::Aes128Sha256RsaOaep,
            ),
            ("aes256_sha256_rsa_pss", _SecurityPolicy::Aes256Sha256RsaPss),
        ];

        for (value, expected) in cases {
            assert_eq!(parse_security_policy(value).unwrap(), expected);
        }
        assert!(parse_security_policy("invalid").is_err());
    }

    #[test]
    fn node_queries_parse_as_ids_or_browse_paths() {
        assert!(matches!(
            parse_node_query("ns=2;s=temperature").expect("node ID should parse"),
            NodeQuery::NodeId(_)
        ));
        assert!(matches!(
            parse_node_query("/Objects/2:Temperature").expect("browse path should parse"),
            NodeQuery::BrowsePath(_)
        ));
        assert!(parse_node_query("not-a-node-id").is_err());
        assert!(parse_node_query("/Objects/").is_err());
    }

    #[test]
    fn durations_must_be_finite_and_non_negative() {
        assert_eq!(
            try_parse_duration("timeout", 1.5).unwrap(),
            Duration::from_millis(1_500)
        );
        assert_eq!(try_parse_duration("timeout", 0.0).unwrap(), Duration::ZERO);

        for value in [-1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(try_parse_duration("timeout", value).is_err());
        }
    }

    #[test]
    fn closed_client_lifecycle_is_stable() {
        let runtime = runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("runtime should build");
        let mut client = OpcUaClient {
            endpoint_url: "opc.tcp://localhost:4840".into(),
            runtime,
            client: None,
        };

        assert!(client.closed());
        assert!(client.ensure_open().is_err());
        assert_eq!(
            client.__repr__(),
            "OpcUaClient(endpoint_url=\"opc.tcp://localhost:4840\", closed=true)"
        );

        Python::initialize();
        Python::attach(|py| {
            assert!(client.resolve_browse_path(py, "/Objects").is_err());
            client.close(py).expect("first close should succeed");
            client.close(py).expect("second close should succeed");
            assert!(
                !client
                    .__exit__(py, None, None, None)
                    .expect("context exit should succeed")
            );
        });
        assert!(client.closed());
    }
}
