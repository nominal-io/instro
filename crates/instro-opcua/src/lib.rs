pub mod browse;
pub mod client;
pub mod error;
pub(crate) mod metrics;
pub mod types;

pub use open62541;

use open62541::Certificate;
use open62541::PrivateKey;
use open62541::create_certificate;
use open62541::ua;

pub use crate::error::OpcUaError;
pub use crate::error::Result;

/// Generates a self-signed X.509 certificate/key pair suitable for OPC-UA client authentication.
///
/// Returns a tuple containing the certificate and private key or an error if the certificate generation fails.
pub fn generate_self_signed_cert() -> Result<(Certificate, PrivateKey)> {
    let subject = ua::Array::from_slice(&[
        ua::String::new("C=US").map_err(|e| OpcUaError::GenerateSelfSignedCert(Some(e)))?,
        ua::String::new("O=Nominal").map_err(|e| OpcUaError::GenerateSelfSignedCert(Some(e)))?,
        ua::String::new("CN=Nominal@localhost")
            .map_err(|e| OpcUaError::GenerateSelfSignedCert(Some(e)))?,
    ]);

    let subject_alt_name = ua::Array::from_slice(&[
        ua::String::new("DNS:localhost")
            .map_err(|e| OpcUaError::GenerateSelfSignedCert(Some(e)))?,
        ua::String::new("URI:urn:nominal:connect-opc-ua-client")
            .map_err(|e| OpcUaError::GenerateSelfSignedCert(Some(e)))?,
    ]);

    create_certificate(
        &subject,
        &subject_alt_name,
        &ua::CertificateFormat::PEM,
        None,
    )
    .map_err(|e| OpcUaError::GenerateSelfSignedCert(Some(e)))
}

#[cfg(test)]
mod tests {
    use super::generate_self_signed_cert;

    #[test]
    fn generate_cert_produces_nonempty_keypair() {
        let (certificate, private_key) =
            generate_self_signed_cert().expect("failed to generate self-signed certificate");

        assert!(!certificate.as_bytes().is_empty(), "certificate is empty");
        assert!(!private_key.as_bytes().is_empty(), "private key is empty");
    }
}
