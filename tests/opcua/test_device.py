import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from asyncua.server.server import Server
from asyncua.ua.status_codes import StatusCodes
from pydantic import ValidationError

from instro.unstable.opcua import OpcUaConnectionConfig, OpcUaDevice, OpcUaDeviceConfig, OpcUaError, OpcUaUsernameAuth
from instro.unstable.opcua import device as device_module


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"connection": {"endpoint_url": "https://localhost"}}, ("connection", "endpoint_url")),
        (
            {"connection": {"endpoint_url": "opc.tcp://localhost", "request_timeout_s": 0}},
            ("connection", "request_timeout_s"),
        ),
        ({"nodes": {"temperature": ""}}, ("nodes",)),
        ({"nodes": {"temperature": "/Objects/<"}}, ("nodes",)),
        ({"node": {}}, ("node",)),
    ],
)
def test_invalid_device_config_fails_before_connect(overrides: dict[str, object], field: tuple[str, ...], monkeypatch):
    connect = Mock()
    monkeypatch.setattr(device_module, "OpcUaClient", connect)
    config = {"connection": {"endpoint_url": "opc.tcp://localhost"}, **overrides}

    with pytest.raises(ValidationError) as failure:
        OpcUaDevice("rig", config)

    assert failure.value.errors()[0]["loc"] == field
    connect.connect.assert_not_called()


def test_device_revalidates_existing_config_before_connect():
    connection = OpcUaConnectionConfig(endpoint_url="opc.tcp://localhost")
    config = OpcUaDeviceConfig(connection=connection)
    connection.authentication = OpcUaUsernameAuth(username="operator", password_env="RIG_PASSWORD")

    with pytest.raises(ValidationError, match="Username authentication requires SignAndEncrypt"):
        OpcUaDevice("rig", config)


def test_username_password_is_required_at_open(monkeypatch):
    monkeypatch.delenv("RIG_PASSWORD", raising=False)
    connect = Mock()
    monkeypatch.setattr(device_module, "OpcUaClient", connect)
    config = OpcUaDeviceConfig(
        connection=OpcUaConnectionConfig(
            endpoint_url="opc.tcp://localhost",
            security_mode="SignAndEncrypt",
            security_policy="Basic256Sha256",
            authentication=OpcUaUsernameAuth(username="operator", password_env="RIG_PASSWORD"),
        )
    )
    device = OpcUaDevice("rig", config)

    with pytest.raises(RuntimeError, match="RIG_PASSWORD"):
        device.open()

    connect.connect.assert_not_called()


def test_device_opens_from_json_and_binds_in_one_ordered_request(
    endpoint: str, server: Server, tmp_path: Path, monkeypatch
):
    view = server.iserver.view_service
    translate = Mock(wraps=view.translate_browsepaths_to_nodeids)
    monkeypatch.setattr(view, "translate_browsepaths_to_nodeids", translate)
    path = tmp_path / "device.json"
    path.write_text(
        json.dumps(
            {
                "connection": {"endpoint_url": endpoint, "pki_dir": str(tmp_path)},
                "nodes": {"temperature": "/Objects/2:Rig/2:Temperature", "float": "/Objects/2:Rig/2:Float"},
            }
        ),
        encoding="utf-8",
    )

    with OpcUaDevice("rig", path) as device:
        translate.assert_called_once()
        browse_paths = translate.call_args.args[0]
        assert [item.RelativePath.Elements[-1].TargetName.Name for item in browse_paths] == ["Temperature", "Float"]
        assert device.client.get_node("/Objects/2:Rig/2:Temperature").value.value == 23.5

    with pytest.raises(RuntimeError):
        _ = device.client


def test_failed_path_binding_disconnects_session(
    endpoint: str, server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    baseline_sessions = set(server.iserver._external_sessions)
    view = server.iserver.view_service
    translate = view.translate_browsepaths_to_nodeids
    binding_sessions = set()

    def capture_session_and_translate(paths):
        binding_sessions.update(set(server.iserver._external_sessions) - baseline_sessions)
        return translate(paths)

    monkeypatch.setattr(view, "translate_browsepaths_to_nodeids", capture_session_and_translate)
    device = OpcUaDevice(
        "rig",
        {
            "connection": {"endpoint_url": endpoint, "pki_dir": tmp_path},
            "nodes": {"temperature": "/Objects/2:Rig/2:Temperature", "missing": "/Objects/2:Missing"},
        },
    )

    with pytest.raises(OpcUaError) as failure:
        device.open()

    assert failure.value.status_code.code == StatusCodes.BadNoMatch
    assert len(binding_sessions) == 1
    assert binding_sessions.isdisjoint(server.iserver._external_sessions)
    with pytest.raises(RuntimeError):
        _ = device.client
