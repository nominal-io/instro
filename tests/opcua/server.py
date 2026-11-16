from pathlib import Path

from asyncua import ua
from asyncua.server.server import Server
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from typing_extensions import Any

CERT_DIR = Path(__file__).with_name("certs")


async def start_server() -> Server:
    server = Server()
    server.disable_clock()
    server.iserver.max_keep_alive_count = 1

    await server.init()
    await server.set_application_uri("urn:instro:test-server")
    server.set_endpoint("opc.tcp://127.0.0.1:0/instro-tests/")
    await server.load_certificate(CERT_DIR / "server-cert.pem")

    # The checked-in fixture does not need repeated RSA factor validation.
    server.iserver.private_key = load_pem_private_key(
        (CERT_DIR / "server-key.pem").read_bytes(), password=None, unsafe_skip_rsa_key_validation=True
    )

    server.set_security_policy(
        [
            ua.SecurityPolicyType.NoSecurity,
            ua.SecurityPolicyType.Basic256Sha256_Sign,
            ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
        ]
    )

    namespace = ua.Int16(await server.register_namespace("urn:instro:tests"))
    rig = await server.nodes.objects.add_folder(
        ua.NodeId(ua.String("Rig"), ua.Int16(namespace)), ua.QualifiedName("Rig", namespace)
    )

    nodes: list[tuple[ua.String, Any]] = [
        (ua.String(name), value) for name, value in [("Temperature", 23.5), ("Float", 1.25), ("Boolean", True)]
    ]

    for name, value in nodes:
        await rig.add_variable(ua.NodeId(name, namespace), ua.QualifiedName(name, namespace), value)

    await server.start()

    return server
