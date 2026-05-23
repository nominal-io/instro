"""Unit tests for unstable EtherNet/IP route-path config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from instro.unstable.ethernetip import EtherNetIPConnectionInfo, EtherNetIPDevice, EtherNetIPRoutePath
from tests.ethernetip_fakes import install_fake_native_ethernetip


def test_route_path_accepts_explicit_backplane_hops() -> None:
    conn = EtherNetIPConnectionInfo.model_validate(
        {
            "host": "192.0.2.10",
            "route_path": {
                "hops": [
                    {"type": "backplane", "slot": 2},
                    {"type": "backplane", "slot": 0},
                ],
            },
        }
    )

    assert conn.address == "192.0.2.10:44818"
    assert conn.route_path is not None
    assert [hop.slot for hop in conn.route_path.hops] == [2, 0]


def test_connection_info_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError) as exc_info:
        EtherNetIPConnectionInfo.model_validate({"host": "192.0.2.10", "timeout": 3.0})

    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_route_path_rejects_explicit_cip_port() -> None:
    with pytest.raises(ValidationError) as exc_info:
        EtherNetIPRoutePath.model_validate({"hops": [{"type": "backplane", "cip_port": 1, "slot": 0}]})

    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_route_path_rejects_out_of_range_backplane_slot() -> None:
    with pytest.raises(ValidationError) as exc_info:
        EtherNetIPRoutePath.model_validate({"hops": [{"type": "backplane", "slot": 256}]})

    assert "255" in str(exc_info.value)


def test_route_path_rejects_unsupported_hop_type() -> None:
    with pytest.raises(ValidationError) as exc_info:
        EtherNetIPRoutePath.model_validate({"hops": [{"type": "ethernet", "address": "10.10.20.5"}]})

    assert "backplane" in str(exc_info.value)


def test_open_passes_normalized_route_path_slots_to_native_session(monkeypatch: pytest.MonkeyPatch) -> None:
    native = install_fake_native_ethernetip(monkeypatch)

    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {
                "host": "192.0.2.10",
                "route_path": {
                    "hops": [
                        {"type": "backplane", "slot": 2},
                        {"type": "backplane", "slot": 0},
                    ],
                },
            },
        }
    )

    instrument.open()

    assert native.sessions == [("192.0.2.10:44818", [2, 0])]
