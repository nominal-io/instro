import sys
from pathlib import Path
from queue import Queue
from typing import Any

import pytest
from pydantic import ValidationError

from instro.lib.publishers.channel_buffer import ChannelValueTimeoutError, DequeInMemoryPublisher
from instro.unstable.opcua import OpcUaBasicAuthConfig, OpcUaConfig, OpcUaConnectionConfig, OpcUaDevice
from tests.opcua.server import OpcUaTestServer


@pytest.fixture
def config_data(endpoint: str) -> dict[str, Any]:
    return {
        "node_paths": ["/Objects/2:Rig/2:Temperature"],
        "connection": {
            "endpoint_url": endpoint,
            "timeout": 2.0,
            "security_mode": "none",
            "security_policy": "none",
            "authentication": {"mode": "anonymous"},
        },
        "sampling_strategy": {"sampling_rate": 20.0},
    }


@pytest.mark.parametrize("source", ["model", "path", "string"])
def test_device_config_sources(config_data: dict[str, Any], tmp_path: Path, source: str) -> None:
    config = OpcUaConfig.model_validate(config_data)
    path = tmp_path / "opcua.json"
    path.write_text(config.model_dump_json())
    selected = {"model": config, "path": path, "string": str(path)}[source]
    with OpcUaDevice("rig", selected) as device:
        client = device.client
        assert client.get_node("/Objects/2:Rig/2:Temperature").value.value == 23.5
    with pytest.raises(RuntimeError, match="before accessing the client"):
        _ = device.client
    with pytest.raises(RuntimeError, match="closed"):
        client.get_node("/Objects/2:Rig")


def test_device_publishes_measurements(config_data: dict[str, Any]) -> None:
    config_data["node_paths"] = [
        "/Objects/2:Rig/2:Timed",
        "/Objects/2:Rig/2:Epoch",
        "/Objects/2:Rig/2:ServerTimed",
        "/Objects/2:Rig/2:Boolean",
        "/Objects/2:Rig/2:Int32",
        "/Objects/2:Rig/2:String",
    ]
    publisher = DequeInMemoryPublisher(maxlen=10)
    with OpcUaDevice("rig", OpcUaConfig.model_validate(config_data), publishers=[publisher]) as device:
        device.start()
        expected = [
            ("BrowsePath(starting_node='i=84' relative_path='/Objects/2:Rig/2:Timed')", 12.5, 1_250_000_000),
            ("BrowsePath(starting_node='i=84' relative_path='/Objects/2:Rig/2:Epoch')", 8.5, 0),
            ("BrowsePath(starting_node='i=84' relative_path='/Objects/2:Rig/2:ServerTimed')", 8.5, 7_000_000_000),
            ("BrowsePath(starting_node='i=84' relative_path='/Objects/2:Rig/2:Boolean')", 1.0, 4_000_000_000),
            (
                "BrowsePath(starting_node='i=84' relative_path='/Objects/2:Rig/2:Int32')",
                -2_000_000_000.0,
                4_000_000_000,
            ),
            ("BrowsePath(starting_node='i=84' relative_path='/Objects/2:Rig/2:String')", "pressure µ", 4_000_000_000),
        ]
        for channel, value, timestamp in expected:
            measurement = publisher.get(channel, timeout=5)
            assert measurement.channel_data == {channel: [value]}
            assert type(measurement.latest) is type(value)
            assert measurement.timestamps == [timestamp]
        device.stop()


def test_device_stop_and_restart(config_data: dict[str, Any], opcua_server: OpcUaTestServer) -> None:
    publisher = DequeInMemoryPublisher(maxlen=10)
    channel = "BrowsePath(starting_node='i=84' relative_path='/Objects/2:Rig/2:Temperature')"
    with OpcUaDevice("rig", OpcUaConfig.model_validate(config_data), publishers=[publisher]) as device:
        device.start()
        assert publisher.get(channel, timeout=5).latest == 23.5
        device.stop()
        try:
            opcua_server.set_temperature(31.75)
            with pytest.raises(ChannelValueTimeoutError):
                publisher.get(channel, length=2, timeout=0.3)
            device.start()
            assert publisher.get(channel, length=2, timeout=5).latest == 31.75
            device.stop()
        finally:
            opcua_server.set_temperature(23.5)


def test_device_applies_subscription_config(config_data: dict[str, Any], opcua_server: OpcUaTestServer) -> None:
    config_data["sampling_strategy"] = {
        "sampling_rate": 20.0,
        "publish_rate": 8.0,
        "lifetime_count": 123,
        "max_keep_alive_count": 7,
        "max_notifications_per_publish": 9,
        "priority": 12,
        "queue_size": 3,
        "discard_oldest": False,
    }
    with OpcUaDevice("rig", OpcUaConfig.model_validate(config_data)) as device:
        device.start()
        try:
            assert opcua_server.request("subscription_parameters") == {
                "subscription": (125.0, 123, 7, 9, 12),
                "items": [(50.0, 3, False)],
            }
        finally:
            device.stop()


@pytest.mark.parametrize(
    "path, message",
    [
        ("/Objects/2:Rig/2:NoTimestamp", "source or server timestamp is required"),
        ("/Objects/2:Rig/2:Guid", "value must be a boolean, integer, float, or string"),
    ],
)
def test_device_rejects_unpublishable_samples(
    config_data: dict[str, Any], path: str, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: Queue[BaseException] = Queue()
    monkeypatch.setattr(sys, "unraisablehook", lambda args: errors.put(args.exc_value))
    config_data["node_paths"] = [path]
    publisher = DequeInMemoryPublisher(maxlen=10)
    with OpcUaDevice("rig", OpcUaConfig.model_validate(config_data), publishers=[publisher]) as device:
        device.start()
        try:
            error = errors.get(timeout=5)
            assert isinstance(error, ValueError)
            assert str(error) == message
            assert publisher.channel_names == ()
        finally:
            device.stop()


def test_basic_auth_connection_parameters(config_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTRO_TEST_OPCUA_PASSWORD", "test-only-password")
    connection = config_data["connection"]
    connection.update(
        authentication={"mode": "basic", "username": "operator", "password_env": "INSTRO_TEST_OPCUA_PASSWORD"},
        security_mode="signandencrypt",
        security_policy="basic256sha256",
        unsafe_allow_unencrypted_connections=False,
        use_generated_certificate=True,
        unsafe_ignore_server_cert=True,
    )
    config = OpcUaConnectionConfig.model_validate(connection)
    assert config.as_connection_params() == {
        "endpoint_url": config_data["connection"]["endpoint_url"],
        "timeout": 2.0,
        "security_mode": "signandencrypt",
        "security_policy": "basic256sha256",
        "username": "operator",
        "password": "test-only-password",
        "use_generated_cert": True,
        "ignore_server_cert": True,
    }


def test_missing_password_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSTRO_TEST_OPCUA_PASSWORD", raising=False)
    with pytest.raises(ValidationError, match="INSTRO_TEST_OPCUA_PASSWORD.*not set"):
        OpcUaBasicAuthConfig(username="operator", password_env="INSTRO_TEST_OPCUA_PASSWORD")


@pytest.mark.parametrize(
    "options, message",
    [
        ({"endpoint_url": "http://127.0.0.1:4840"}, "Invalid endpoint URL scheme"),
        ({"security_mode": "sign", "security_policy": "none"}, "Security policy"),
    ],
)
def test_invalid_connection_config(config_data: dict[str, Any], options: dict[str, Any], message: str) -> None:
    connection = config_data["connection"] | options
    with pytest.raises(ValidationError, match=message):
        OpcUaConnectionConfig.model_validate(connection)


def test_unencrypted_basic_auth_is_rejected(config_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTRO_TEST_OPCUA_PASSWORD", "test-only-password")
    connection = config_data["connection"]
    connection.update(
        authentication={"mode": "basic", "username": "operator", "password_env": "INSTRO_TEST_OPCUA_PASSWORD"},
        unsafe_allow_unencrypted_connections=False,
    )
    with pytest.raises(ValidationError, match="requires encryption"):
        OpcUaConnectionConfig.model_validate(connection)


def test_invalid_node_path_is_rejected(config_data: dict[str, Any]) -> None:
    config_data["node_paths"] = ["/<InvalidReference"]
    with pytest.raises(ValidationError, match="One or more paths were invalid"):
        OpcUaConfig.model_validate(config_data)
