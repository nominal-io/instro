"""Tests for the Python EtherNet/IP bindings."""

from __future__ import annotations

import importlib.util

import pytest

from instro.unstable import _ethernetip as ethernetip

EtherNetIpSession = ethernetip.EtherNetIpSession
PlcKind = ethernetip.PlcKind
PlcValue = ethernetip.PlcValue
StructuredValue = ethernetip.StructuredValue


def test_ethernetip_native_types_use_private_local_import_path() -> None:
    """Native EtherNet/IP bindings should stay private until the published wheel surface exists."""
    assert EtherNetIpSession.__module__ == "instro.unstable._ethernetip"
    assert PlcKind.__module__ == "instro.unstable._ethernetip"
    assert StructuredValue.__module__ == "instro.unstable._ethernetip"
    assert importlib.util.find_spec("instro.ethernetip") is None


def test_plc_value_preserves_explicit_scalar_kinds() -> None:
    """`PlcValue` constructors preserve every supported scalar PLC kind."""
    cases = [
        (PlcValue.bool(False), PlcKind.BOOL, False),
        (PlcValue.sint(-3), PlcKind.SINT, -3),
        (PlcValue.int(-12), PlcKind.INT, -12),
        (PlcValue.dint(1234), PlcKind.DINT, 1234),
        (PlcValue.lint(-5678), PlcKind.LINT, -5678),
        (PlcValue.usint(7), PlcKind.USINT, 7),
        (PlcValue.uint(42), PlcKind.UINT, 42),
        (PlcValue.udint(99), PlcKind.UDINT, 99),
        (PlcValue.ulint(123_456), PlcKind.ULINT, 123_456),
        (PlcValue.real(1.25), PlcKind.REAL, pytest.approx(1.25)),
        (PlcValue.lreal(-9.5), PlcKind.LREAL, pytest.approx(-9.5)),
        (PlcValue.string("hello"), PlcKind.STRING, "hello"),
    ]

    for value, expected_kind, expected_payload in cases:
        assert value.kind == expected_kind
        assert value.value == expected_payload


def test_plc_value_wraps_structured_payload_explicitly() -> None:
    """Structured payloads live inside a structured `PlcValue`, not alongside it."""
    payload = StructuredValue(symbol_id=7, data=b"\x01\x02\x03")
    value = PlcValue.structured(payload)

    assert value.kind == PlcKind.STRUCTURED
    structured = value.value
    assert isinstance(structured, StructuredValue)
    assert structured.symbol_id == 7
    assert structured.data == b"\x01\x02\x03"
