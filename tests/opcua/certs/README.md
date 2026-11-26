# OPC UA test credentials

These intentionally public certificate/key pairs are fixtures for the loopback
OPC UA integration tests. The server and client load separate RSA-2048 pairs so
test runs do not generate keys.

Both certificates are self-signed and valid from January 1, 2020 through
January 1, 2120. Each identifies `localhost` and `127.0.0.1`; the server application
URI is `urn:instro:test-server` and the client URI is
`urn:nominal:instro-opcua-client`. Their extended key usages are server
authentication and client authentication, respectively.

Keep each certificate and its matching unencrypted PKCS#8 private key together
when replacing these fixtures. These credentials are for tests only.
