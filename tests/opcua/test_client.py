from datetime import timedelta
from queue import Queue
from threading import Event
from time import monotonic
from typing import Any

import pytest

from instro.unstable.opcua import Node, OpcUaClient, OpcUaConnectionConfig, get_endpoints
from instro.unstable.opcua.types import (
    AccessLevelExType,
    AccessLevelType,
    AccessRestrictionType,
    ApplicationType,
    AttributeId,
    AttributeWriteMask,
    DataValue,
    EventNotifierType,
    MessageSecurityMode,
    NodeClass,
    RelativePath,
)
from tests.opcua.server import OpcUaTestServer


def test_discovery(endpoint: str) -> None:
    endpoints = get_endpoints(endpoint)
    assert len(endpoints) == 1
    description = endpoints[0]
    assert description.endpoint_url == endpoint
    assert description.application_name == "Instro Python tests"
    assert description.application_uri == "urn:instro:test-server"
    assert description.product_uri == "urn:instro:test-product"
    assert description.application_type == ApplicationType.SERVER
    assert description.security_mode == MessageSecurityMode.NONE
    assert description.security_policy == "http://opcfoundation.org/UA/SecurityPolicy#None"
    assert description.server_certificate == b""


@pytest.mark.parametrize("timeout", [2.0, timedelta(seconds=2)])
def test_connect_and_disconnect(endpoint: str, timeout: float | timedelta) -> None:
    with OpcUaClient.connect(endpoint, security_mode=MessageSecurityMode.NONE, timeout=timeout) as client:
        node = client.objects.get_child("/2:Rig/2:Temperature")
        assert node.value.value == 23.5
    with pytest.raises((RuntimeError, ValueError), match="closed|disconnect"):
        node.get_attribute(AttributeId.VALUE)


def test_connection_config_allows_opted_in_unencrypted_basic_auth(
    endpoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSTRO_TEST_OPCUA_PASSWORD", "test-only-password")
    config = OpcUaConnectionConfig(
        endpoint_url=endpoint,
        timeout=2.0,
        security_mode="none",
        security_policy="none",
        unsafe_allow_unencrypted_connections=True,
        authentication={
            "mode": "basic",
            "username": "test-operator",
            "password_env": "INSTRO_TEST_OPCUA_PASSWORD",
        },
    )
    with OpcUaClient.connect(**config.as_connection_params()) as client:
        assert client.objects.get_child("/2:Rig/2:Temperature").value.value == 23.5


@pytest.mark.parametrize("security_mode", ["sign", "signandencrypt"])
def test_secure_basic_authentication(secure_server: OpcUaTestServer, security_mode: str) -> None:
    options = {
        "security_mode": security_mode,
        "security_policy": "basic256sha256",
        "use_generated_cert": True,
        "ignore_server_cert": True,
        "username": "test-operator",
        "timeout": 2.0,
    }
    with OpcUaClient.connect(secure_server.endpoint, password="test-only-password", **options) as client:
        assert client.objects.get_child("/2:Rig/2:Temperature").value.value == 23.5
    with pytest.raises(RuntimeError, match="BadUserAccessDenied"):
        OpcUaClient.connect(secure_server.endpoint, password="wrong-password", **options)


@pytest.mark.parametrize("folder, identifier", [("objects", 85), ("types", 86), ("views", 87)])
def test_standard_folders(client: OpcUaClient, folder: str, identifier: int) -> None:
    node = getattr(client, folder)
    assert node.node_id.namespace_index == 0
    assert node.node_id.identifier == identifier


def test_relative_browsing_is_scoped_to_parent(client: OpcUaClient) -> None:
    rig = client.get_node(RelativePath.parse("/Objects/2:Rig"))
    child = rig.get_child(RelativePath.parse("/2:Temperature"))
    assert child.node_id.identifier == "temperature"
    assert child.value.value == 23.5
    assert client.objects.get_child("/2:Other/2:Temperature").value.value == -10.0

    path = child.browse_path
    assert path.starting_node.identifier == 84
    assert [
        (element.target_name.namespace_index, element.target_name.name) for element in path.relative_path.elements
    ] == [
        (0, "Objects"),
        (2, "Rig"),
        (2, "Temperature"),
    ]
    assert client.get_node(path.relative_path).node_id == child.node_id


def test_children_retain_usable_paths(client: OpcUaClient) -> None:
    children = client.objects.get_child("/2:Other").children
    assert [child.node_id.identifier for child in children] == ["other-temperature"]
    child = children[0]
    assert client.get_node(child.browse_path.relative_path).value.value == -10.0
    assert child.children == []


def test_node_attribute_wrappers(client: OpcUaClient) -> None:
    node = client.get_node("/Objects/2:Rig/2:Temperature")
    assert (node.browse_name.namespace_index, node.browse_name.name) == (2, "Temperature")
    assert (node.display_name.locale, node.display_name.text) == ("de-DE", "Temperatur")
    assert (node.description.locale, node.description.text) == ("en-US", "Probe reading")
    assert node.node_class == NodeClass.VARIABLE
    assert node.data_type.identifier == 11
    assert node.write_mask == AttributeWriteMask.DISPLAY_NAME
    assert node.user_write_mask == AttributeWriteMask.DESCRIPTION
    assert node.access_level == AccessLevelType.CURRENT_READ
    assert node.user_access_level == AccessLevelType.NONE
    assert node.access_level_ex == AccessLevelExType.CURRENT_READ | AccessLevelExType.NONATOMIC_WRITE
    assert node.access_restrictions == AccessRestrictionType.SIGNING_REQUIRED | AccessRestrictionType.SESSION_REQUIRED
    assert node.minimum_sampling_interval == timedelta(milliseconds=250)
    assert node.historizing is True

    folder = client.objects.get_child("/2:Rig")
    assert (folder.description.locale, folder.description.text) == (None, "Test rig")
    assert folder.event_notifier == EventNotifierType.SUBSCRIBE_TO_EVENTS | EventNotifierType.HISTORY_READ
    assert folder.value is None
    assert folder.data_type is None
    assert node.executable is None


def test_node_class_specific_attributes(client: OpcUaClient) -> None:
    rig = client.objects.get_child("/2:Rig")
    assert rig.get_child("/2:PumpType").is_abstract is True
    reference = rig.get_child("/2:Owns")
    assert reference.symmetric is False
    assert reference.inverse_name.text == "OwnedBy"
    method = rig.get_child("/2:Reset")
    assert method.executable is True
    assert method.user_executable is False
    assert rig.get_child("/2:LiveView").contains_no_loops is True


@pytest.mark.parametrize("attribute", [AttributeId.VALUE, 13, "VALUE"])
def test_attribute_selector_forms(client: OpcUaClient, attribute: AttributeId | int | str) -> None:
    value = client.get_node("/Objects/2:Rig/2:Temperature").get_attribute(attribute)
    assert value.value == 23.5


@pytest.mark.parametrize("attribute", [0, "not_an_attribute", object()])
def test_invalid_attribute_selectors(client: OpcUaClient, attribute: Any) -> None:
    with pytest.raises(ValueError, match="invalid attribute id"):
        client.objects.get_attribute(attribute)


def test_missing_browse_target(client: OpcUaClient) -> None:
    with pytest.raises(ValueError, match="BadNoMatch"):
        client.get_node("/Objects/2:DoesNotExist")


def test_empty_child_path(client: OpcUaClient) -> None:
    with pytest.raises(ValueError, match="invalid relative path"):
        client.objects.get_child(RelativePath.parse(""))


@pytest.mark.parametrize(
    "options, message",
    [
        ({"username": "operator"}, "username.*password.*together"),
        ({"cert": b"certificate"}, "cert.*private_key.*together"),
        ({"timeout": "two seconds"}, "invalid timeout"),
        ({"security_mode": "invalid"}, "invalid attribute id"),
        ({"security_policy": "invalid"}, "invalid security policy"),
    ],
)
def test_invalid_connection_options(endpoint: str, options: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        OpcUaClient.connect(endpoint, **options)


def test_subscription_delivers_mixed_node_specifications(client: OpcUaClient, opcua_server: OpcUaTestServer) -> None:
    samples: Queue[list[tuple[Node, DataValue]]] = Queue()
    after_stop = Event()

    def on_data(points: list[tuple[Node, DataValue]]) -> None:
        samples.put(points)
        if any(node.node_id.identifier == "temperature" and value.value == 39.0 for node, value in points):
            after_stop.set()

    temperature = client.get_node("/Objects/2:Rig/2:Temperature")
    with client.subscribe(
        on_data,
        [temperature, "/Objects/2:Rig/2:Boolean", RelativePath.parse("/Objects/2:Rig/2:UInt64")],
        freq_hz=20,
        lifetime_count=100,
        max_keep_alive_count=10,
        queue_size=4,
    ):
        received = {}
        deadline = monotonic() + 5
        while len(received) < 3 and monotonic() < deadline:
            for node, value in samples.get(timeout=max(0.01, deadline - monotonic())):
                received[node.node_id.identifier] = value.value
        assert received == {"temperature": 23.5, "Boolean": True, "UInt64": 18_000_000_000_000_000_000}

        try:
            opcua_server.set_temperature(31.75)
            deadline = monotonic() + 5
            while monotonic() < deadline:
                batch = samples.get(timeout=max(0.01, deadline - monotonic()))
                if any(node.node_id.identifier == "temperature" and value.value == 31.75 for node, value in batch):
                    break
            else:
                pytest.fail("subscription did not deliver the changed temperature")
        finally:
            opcua_server.set_temperature(23.5)

    try:
        opcua_server.set_temperature(39.0)
        assert not after_stop.wait(timeout=0.3)
    finally:
        opcua_server.set_temperature(23.5)


def test_subscription_rejects_foreign_nodes(client: OpcUaClient, endpoint: str) -> None:
    with OpcUaClient.connect(endpoint, timeout=2.0) as other:
        with pytest.raises(ValueError, match="same client"):
            client.subscribe(lambda points: None, [other.objects], freq_hz=20)


@pytest.mark.parametrize("frequency_option", ["freq_hz", "bg_poll_freq_hz", "publish_freq_hz"])
def test_subscription_rejects_invalid_frequencies(client: OpcUaClient, frequency_option: str) -> None:
    options = {"freq_hz": 20.0, frequency_option: 0.0}
    with pytest.raises(ValueError, match="frequency must be finite and positive"):
        client.subscribe(lambda points: None, [client.objects], **options)


def test_subscription_rejects_noncallable_receiver(client: OpcUaClient) -> None:
    with pytest.raises(ValueError, match="on_data.*callable"):
        client.subscribe(None, [client.objects], freq_hz=20)
