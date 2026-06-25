pub mod browse;
pub mod client;
pub(crate) mod metrics;
pub mod types;

use anyhow::Context as _;
use anyhow::Result;
use open62541::Certificate;
use open62541::PrivateKey;
use open62541::create_certificate;
use open62541::ua;

/// Generates a self-signed X.509 certificate/key pair suitable for OPC-UA client authentication.
///
/// Returns a tuple containing the certificate and private key or an error if the certificate generation fails.
pub fn generate_self_signed_cert() -> Result<(Certificate, PrivateKey)> {
    let subject = ua::Array::from_slice(&[
        ua::String::new("C=US").context("building client cert subject")?,
        ua::String::new("O=Nominal").context("building client cert subject")?,
        ua::String::new("CN=Nominal@localhost").context("building client cert subject")?,
    ]);

    let subject_alt_name = ua::Array::from_slice(&[
        ua::String::new("DNS:localhost").context("building client cert SAN")?,
        ua::String::new("URI:urn:nominal:connect-opc-ua-client")
            .context("building client cert SAN")?,
    ]);

    create_certificate(
        &subject,
        &subject_alt_name,
        &ua::CertificateFormat::PEM,
        None,
    )
    .context("generating client certificate")
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
