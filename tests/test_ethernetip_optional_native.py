"""Tests for optional EtherNet/IP native bindings."""

from __future__ import annotations

import importlib
import sys

import pytest

from instro.ethernetip import EtherNetIPDevice


def test_hal_imports_without_native_ethernetip(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the native EtherNet/IP module being unavailable.
    monkeypatch.setitem(sys.modules, "instro.ethernetip._ethernetip", None)

    # The pure-Python HAL (config, types, EtherNetIPDevice) must stay importable
    # even when the native backend is not installed.
    ethernetip = importlib.import_module("instro.ethernetip")

    assert hasattr(ethernetip, "EtherNetIPDevice")
    assert hasattr(ethernetip, "EtherNetIPConfig")


def test_open_reports_extra_when_native_ethernetip_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the EtherNet/IP module missing.
    monkeypatch.setitem(sys.modules, "instro.ethernetip._ethernetip", None)
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
        }
    )

    with pytest.raises(RuntimeError) as exc_info:
        instrument.open()

    # The failure should tell users which package is missing and the extra that installs it.
    message = str(exc_info.value)
    assert "instro-ethernetip" in message
    assert "instro[ethernetip]" in message
