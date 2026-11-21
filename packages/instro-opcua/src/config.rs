use std::hash::Hash;
use std::hash::Hasher;
use std::mem::discriminant;

use opcua::crypto::SecurityPolicy as UaSecurityPolicy;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::values::UaType;

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
    skip_from_py_object,
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

impl<'a, 'py> FromPyObject<'a, 'py> for SecurityPolicy {
    type Error = pyo3::pyclass::PyClassGuardError<'a, 'py>;

    fn extract(obj: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        Ok(*obj.extract::<PyClassGuard<'_, Self>>()?)
    }
}

impl UaType for SecurityPolicy {
    type Inner = UaSecurityPolicy;

    fn new(value: Self::Inner) -> Self {
        Self(value)
    }

    fn as_ua_ref(&self) -> &Self::Inner {
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

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<SecurityPolicy>()
}
