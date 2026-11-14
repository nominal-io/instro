from datetime import timedelta
from pathlib import Path
from threading import Timer
from typing import Any
from uuid import UUID

import pytest

from instro.unstable.opcua import IdentityToken, OpcUaClient, OpcUaError, get_endpoints
from instro.unstable.opcua.types import AccessLevelExType, AttributeId, MessageSecurityMode, QualifiedName, RelativePath
from tests.opcua.server import CERT_DIR, OpcUaTestServer


@pytest.mark.parametrize("timeout", [2.0, timedelta(seconds=2)])
def test_connect_and_disconnect(endpoint: str, timeout: float | timedelta) -> None:
    with OpcUaClient.connect(
        endpoint,
        security_mode=MessageSecurityMode.NONE,
        request_timeout=timeout,
    ) as client:
        node = client.objects.get_child("/2:Rig/2:Temperature")

        assert node.value.value == 23.5

    with pytest.raises(OpcUaError, match="disconnected"):
        node.get_attribute(AttributeId.VALUE)


@pytest.mark.parametrize("security_mode", ["sign", "signandencrypt"])
def test_secure_basic_authentication(opcua_server: OpcUaTestServer, security_mode: str, tmp_path: Path) -> None:
    with OpcUaClient.connect(
        opcua_server.endpoint,
        identity=IdentityToken.user_name("test-operator", "test-only-password"),
        security_mode=security_mode,
        security_policy="basic256sha256",
        certificate_path=CERT_DIR / "client-cert.pem",
        private_key_path=CERT_DIR / "client-key.pem",
        verify_server_certs=False,
        trust_server_certs=True,
        pki_dir=tmp_path / "pki",
        application_uri="urn:nominal:instro-opcua-client",
        request_timeout=2.0,
        session_retry_limit=0,
    ) as client:
        assert client.objects.get_child("/2:Rig/2:Temperature").value.value == 23.5


def test_relative_browsing_is_scoped_to_parent(client: OpcUaClient) -> None:
    rig = client.get_node(
        RelativePath.from_qualified_names(
            [QualifiedName(0, "Objects"), QualifiedName(2, "Rig")],
        ),
    )
    child = rig.get_child(RelativePath.parse("/2:Temperature"))

    with pytest.raises(OpcUaError) as error:
        client.objects.get_child("/2:Temperature")

    assert error.value.status_code.name() == "BadNoMatch"

    path = child.browse_path

    assert client.get_node(path.relative_path).node_id == child.node_id


def test_children_retain_usable_paths(client: OpcUaClient) -> None:
    children = client.objects.get_child("/2:Rig").children
    child = next(child for child in children if child.node_id.identifier == "Boolean")

    assert client.get_node(child.browse_path.relative_path).value.value is True
    assert child.children == []


@pytest.mark.parametrize("attribute", [AttributeId.VALUE, 13, "VALUE"])
def test_attribute_selector_forms(client: OpcUaClient, attribute: AttributeId | int | str) -> None:
    value = client.get_node("/Objects/2:Rig/2:Temperature").get_attribute(attribute)

    assert value.attribute_id == AttributeId.VALUE
    assert value.value == 23.5


@pytest.mark.parametrize(
    "attribute, error",
    [
        (0, ValueError),
        ("not_an_attribute", ValueError),
        (object(), TypeError),
    ],
)
def test_invalid_attribute_selectors(client: OpcUaClient, attribute: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        client.objects.get_attribute(attribute)


def test_empty_child_path(client: OpcUaClient) -> None:
    with pytest.raises(ValueError, match="relative path must contain"):
        client.objects.get_child(RelativePath.parse(""))


def test_subscription_delivers_mixed_node_specifications(client: OpcUaClient, opcua_server: OpcUaTestServer) -> None:
    temperature = client.get_node("/Objects/2:Rig/2:Temperature")

    specifications = (temperature, "/Objects/2:Rig/2:Boolean", RelativePath.parse("/Objects/2:Rig/2:UInt64"))

    with client.subscribe(
        iter(specifications),
        publishing_interval=0.05,
        sampling_interval=50.0,
        lifetime_count=100,
        max_keep_alive_count=10,
        max_notifications_per_publish=9,
        priority=12,
        queue_size=4,
    ) as subscription:
        deadline = Timer(5, subscription.close)
        deadline.start()

        try:
            received: dict[object, object] = {}
            while len(received) < 3:
                node, value = next(subscription)
                received[node.node_id.identifier] = value.value

            assert received == {"Temperature": 23.5, "Boolean": True, "UInt64": 18_000_000_000_000}
            assert opcua_server.request("subscription_parameters") == {
                "subscription": (50.0, 100, 10, 9, 12),
                "items": [(50.0, 4, False), (50.0, 4, False), (50.0, 4, False)],
            }
        finally:
            deadline.cancel()


def test_subscription_rejects_foreign_nodes(client: OpcUaClient, endpoint: str) -> None:
    with OpcUaClient.connect(endpoint, request_timeout=2.0) as other:
        with pytest.raises(ValueError, match="belong to this client"):
            client.subscribe([other.objects])


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Boolean", True),
        ("Int64", -9_000_000_000_000),
        ("UInt64", 18_000_000_000_000),
        ("Float", 1.25),
        ("String", "pressure µ"),
        ("Guid", UUID("12345678-1234-5678-9abc-def012345678")),
        ("DateTime", 5_125_000_000),
        ("Array", [1, 2]),
        ("Bytes", b"\x00\xff\x07"),
    ],
)
def test_value_conversion(client: OpcUaClient, name: str, expected: object) -> None:
    value = client.get_node(f"/Objects/2:Rig/2:{name}").value.value

    assert type(value) is type(expected)
    assert value == expected


def test_semantic_attribute_dispatch(client: OpcUaClient) -> None:
    node = client.get_node("/Objects/2:Rig/2:Temperature")

    assert node.get_attribute("ACCESS_LEVEL_EX").value == (
        AccessLevelExType.CURRENT_READ | AccessLevelExType.NONATOMIC_WRITE
    )

    assert node.minimum_sampling_interval.value == timedelta(milliseconds=250.5)


@pytest.mark.parametrize(
    "name, source, server",
    [
        ("SourceTimestamped", 0, 7_000_000_000),
        ("ServerTimestamped", None, 7_000_000_000),
        ("NoTimestamp", None, None),
    ],
)
def test_timestamp_presence(client: OpcUaClient, name: str, source: int | None, server: int | None) -> None:
    value = client.get_node(f"/Objects/2:Rig/2:{name}").value

    assert value.source_unix_timestamp == source
    assert value.server_unix_timestamp == server
    assert (value.source_timestamp is None) == (source is None)
    assert (value.server_timestamp is None) == (server is None)


def test_bad_quality_preserves_result_metadata(client: OpcUaClient) -> None:
    bad = client.get_node("/Objects/2:Rig/2:BadData").value

    assert bad.status.code == 0x803A0000
    assert bad.source_unix_timestamp == 123_456_000


def test_unsupported_payload_defers_failure(client: OpcUaClient) -> None:
    unsupported = client.get_node("/Objects/2:Rig/2:NestedDataValue").value

    assert unsupported.source_unix_timestamp == 123_456_000

    with pytest.raises(OpcUaError) as error:
        _ = unsupported.value

    assert error.value.status_code.name() == "BadNotSupported"


def test_enum_definition_conversion(client: OpcUaClient) -> None:
    enum: Any = client.get_node("/Objects/2:Rig/2:Mode").data_type_definition.value

    assert [(field.name, field.value) for field in enum.fields] == [("Off", -7), ("On", 42)]


def test_connect_rejects_unknown_keyword(endpoint: str) -> None:
    with pytest.raises(TypeError):
        OpcUaClient.connect(endpoint, username="operator")  # type: ignore[call-arg]


def test_discovery_security_modes(endpoint: str) -> None:
    descriptions = get_endpoints(endpoint)

    assert {description.security_mode for description in descriptions} == {
        MessageSecurityMode.NONE,
        MessageSecurityMode.SIGN,
        MessageSecurityMode.SIGN_AND_ENCRYPT,
    }
