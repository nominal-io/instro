"""Tests for optional EtherNet/IP native bindings."""

from __future__ import annotations

import importlib
import sys

import pytest

from instro.unstable.ethernetip import EtherNetIPDevice


def test_unstable_imports_do_not_require_native_ethernetip(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the EtherNet/IP module missing.
    monkeypatch.setitem(sys.modules, "instro.unstable._ethernetip", None)

    # Importing unstable packages must stay pure Python. Scope should keep
    # working, and EIP config/types should remain importable, even when the
    # native EIP backend is unavailable.
    scope = importlib.import_module("instro.unstable.scope")
    ethernetip = importlib.import_module("instro.unstable.ethernetip")

    assert hasattr(scope, "InstroScope")
    assert hasattr(ethernetip, "EtherNetIPDevice")


def test_open_reports_extra_when_native_ethernetip_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the EtherNet/IP module missing.
    monkeypatch.setitem(sys.modules, "instro.unstable._ethernetip", None)
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
        }
    )

    with pytest.raises(RuntimeError) as exc_info:
        instrument.open()

    # The failure should tell users which package is missing and the preferred
    # `instro-unstable` extra that installs it.
    message = str(exc_info.value)
    assert "instro-ethernetip" in message
    assert "instro-unstable[ethernetip]" in message
