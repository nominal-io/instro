use std::hash::Hash;
use std::hash::Hasher;
use std::mem::discriminant;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration as StdDuration;

use opcua::client::IdentityToken as UaIdentityToken;
use opcua::crypto::SecurityPolicy as UaSecurityPolicy;
use pyo3::exceptions::PyTypeError;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBool;
use pyo3::types::PyDelta;
use pyo3::types::PyFloat;
use pyo3::types::PyInt;

use crate::time::Duration;

pub(crate) const APPLICATION_NAME: &str = "Nominal OPC UA Client";
pub(crate) const APPLICATION_URI: &str = "urn:nominal:opcua-client";

pub(crate) fn pki_dir() -> Option<PathBuf> {
    let Some(base) = dirs::data_local_dir() else {
        tracing::warn!(
            "Cannot locate the local data directory; using the native OPC UA PKI default"
        );
        return None;
    };

    Some(base.join("instro").join("opcua"))
}

/// Credentials used to authenticate one OPC UA session.
///
/// Create credentials with ``anonymous()``, ``user_name()``, or ``x509()``.
///
/// Examples:
///     >>> identity = IdentityToken.user_name("operator", "password")
///     >>> client = OpcUaClient.connect("opc.tcp://localhost:4840", identity=identity)
#[pyclass(frozen, from_py_object, module = "instro.unstable.opcua")]
#[derive(Clone)]
pub(crate) struct IdentityToken(UaIdentityToken);

impl From<IdentityToken> for UaIdentityToken {
    fn from(value: IdentityToken) -> Self {
        value.0
    }
}

#[pymethods]
impl IdentityToken {
    /// Create an anonymous identity.
    ///
    /// Returns:
    ///     IdentityToken: Anonymous session credentials.
    #[staticmethod]
    fn anonymous() -> Self {
        Self(UaIdentityToken::Anonymous)
    }

    /// Create an identity from a username and password.
    ///
    /// Args:
    ///     username (str): User name, including an empty name if the server permits it.
    ///     password (str): Password, including an empty password if the server permits it.
    ///
    /// Returns:
    ///     IdentityToken: Username session credentials.
    #[staticmethod]
    fn user_name(username: String, password: String) -> Self {
        Self(UaIdentityToken::new_user_name(username, password))
    }

    /// Read a user certificate and private key from filesystem paths.
    ///
    /// These credentials authenticate the user, separately from the application's
    /// certificate configured on the client.
    ///
    /// Args:
    ///     certificate_path (str | os.PathLike[str]): DER or PEM certificate path.
    ///     private_key_path (str | os.PathLike[str]): PEM private key path.
    ///
    /// Returns:
    ///     IdentityToken: Certificate session credentials.
    #[staticmethod]
    fn x509(certificate_path: PathBuf, private_key_path: PathBuf) -> PyResult<Self> {
        UaIdentityToken::new_x509_path(certificate_path, private_key_path)
            .map(Self)
            .map_err(crate::error::native)
    }
}

/// A supported OPC UA security policy, with case-insensitive name matching.
///
/// Accepts native policy names and full URIs. The AES policies also accept
/// ``Aes128Sha256RsaOaep`` and ``Aes256Sha256RsaPss`` without hyphens.
///
/// Args:
///     name (str): Case-insensitive canonical policy name, full URI, or supported AES alias.
///
/// Examples:
///     >>> SecurityPolicy("Basic256Sha256").name
///     'Basic256Sha256'
#[pyclass(
    frozen,
    from_py_object,
    eq,
    hash,
    module = "instro.unstable.opcua.types"
)]
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct SecurityPolicy(UaSecurityPolicy);

impl Hash for SecurityPolicy {
    fn hash<H: Hasher>(&self, state: &mut H) {
        discriminant(&self.0).hash(state);
    }
}

impl From<UaSecurityPolicy> for SecurityPolicy {
    fn from(value: UaSecurityPolicy) -> Self {
        Self(value)
    }
}

impl AsRef<UaSecurityPolicy> for SecurityPolicy {
    fn as_ref(&self) -> &UaSecurityPolicy {
        &self.0
    }
}

#[pymethods]
impl SecurityPolicy {
    /// Select a supported security policy by name or full URI.
    ///
    /// Args:
    ///     name (str): ``None``, ``Basic128Rsa15``, ``Basic256``,
    ///         ``Basic256Sha256``, ``Aes128-Sha256-RsaOaep``, or
    ///         ``Aes256-Sha256-RsaPss``; a matching full URI or AES alias also works.
    ///
    /// Returns:
    ///     SecurityPolicy: The selected policy.
    ///
    /// Examples:
    ///     >>> policy = SecurityPolicy("Basic256Sha256")
    ///     >>> policy.name
    ///     'Basic256Sha256'
    #[new]
    fn new(name: &str) -> PyResult<Self> {
        parse_security_policy(name).map(Self)
    }

    #[classattr]
    const NONE: Self = Self(UaSecurityPolicy::None);

    #[classattr]
    const BASIC128_RSA15: Self = Self(UaSecurityPolicy::Basic128Rsa15);

    #[classattr]
    const BASIC256: Self = Self(UaSecurityPolicy::Basic256);

    #[classattr]
    const BASIC256_SHA256: Self = Self(UaSecurityPolicy::Basic256Sha256);

    #[classattr]
    const AES128_SHA256_RSA_OAEP: Self = Self(UaSecurityPolicy::Aes128Sha256RsaOaep);

    #[classattr]
    const AES256_SHA256_RSA_PSS: Self = Self(UaSecurityPolicy::Aes256Sha256RsaPss);

    #[getter]
    /// str: The native canonical policy name.
    fn name(&self) -> PyResult<&'static str> {
        checked_security_policy(self.0).map(|policy| policy.to_str())
    }

    #[getter]
    /// str: The complete OPC Foundation policy URI.
    fn uri(&self) -> PyResult<&'static str> {
        checked_security_policy(self.0).map(|policy| policy.to_uri())
    }

    fn __str__(&self) -> PyResult<&'static str> {
        self.name()
    }

    fn __repr__(&self) -> PyResult<String> {
        self.name().map(|name| format!("SecurityPolicy({name:?})"))
    }
}

fn checked_security_policy(value: UaSecurityPolicy) -> PyResult<UaSecurityPolicy> {
    if value.is_supported() {
        Ok(value)
    } else {
        Err(PyValueError::new_err("unknown security policy"))
    }
}

fn parse_security_policy(name: &str) -> PyResult<UaSecurityPolicy> {
    const POLICIES: [UaSecurityPolicy; 6] = [
        UaSecurityPolicy::None,
        UaSecurityPolicy::Basic128Rsa15,
        UaSecurityPolicy::Basic256,
        UaSecurityPolicy::Basic256Sha256,
        UaSecurityPolicy::Aes128Sha256RsaOaep,
        UaSecurityPolicy::Aes256Sha256RsaPss,
    ];

    for policy in POLICIES {
        let matches_policy = name.eq_ignore_ascii_case(policy.to_str())
            || name.eq_ignore_ascii_case(policy.to_uri());

        if matches_policy {
            return Ok(policy);
        }
    }

    match name.to_ascii_lowercase().as_str() {
        "aes128sha256rsaoaep" => Ok(UaSecurityPolicy::Aes128Sha256RsaOaep),
        "aes256sha256rsapss" => Ok(UaSecurityPolicy::Aes256Sha256RsaPss),
        _ => Err(PyValueError::new_err(format!(
            "unknown security policy: {name:?}"
        ))),
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct DurationInput(StdDuration);

impl DurationInput {
    pub(crate) const fn from_secs(seconds: u64) -> Self {
        Self(StdDuration::from_secs(seconds))
    }

    pub(crate) const fn from_millis(milliseconds: u64) -> Self {
        Self(StdDuration::from_millis(milliseconds))
    }

    pub(crate) fn into_native(self) -> StdDuration {
        self.0
    }
}

impl FromPyObject<'_, '_> for DurationInput {
    type Error = PyErr;

    fn extract(value: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
        if let Ok(duration) = value.extract::<Duration>() {
            return Ok(Self(*duration.as_ref()));
        }

        if value.is_instance_of::<PyDelta>() {
            return value.extract::<StdDuration>().map(Self);
        }

        if !value.is_instance_of::<PyBool>() && value.is_instance_of::<PyInt>() {
            return value
                .extract::<u64>()
                .map(StdDuration::from_secs)
                .map(Self)
                .map_err(|_| PyValueError::new_err("duration seconds exceed the native range"));
        }

        if value.is_instance_of::<PyFloat>() {
            return StdDuration::try_from_secs_f64(value.extract()?)
                .map(Self)
                .map_err(|_| {
                    PyValueError::new_err(
                        "duration seconds must be finite, nonnegative, and within the native range",
                    )
                });
        }

        Err(PyTypeError::new_err(
            "duration must be a Duration, datetime.timedelta, int, or float",
        ))
    }
}

pub(crate) fn validate_retry_limit(limit: i32) -> PyResult<i32> {
    if limit < -1 {
        Err(PyValueError::new_err(
            "session_retry_limit must be -1, zero, or positive",
        ))
    } else {
        Ok(limit)
    }
}

pub(crate) fn validate_certificate_paths(
    certificate_path: Option<&Path>,
    private_key_path: Option<&Path>,
) -> PyResult<()> {
    if certificate_path.is_some() != private_key_path.is_some() {
        Err(PyValueError::new_err(
            "certificate_path and private_key_path must be supplied together",
        ))
    } else {
        Ok(())
    }
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<SecurityPolicy>()
}
