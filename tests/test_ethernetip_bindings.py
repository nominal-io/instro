"""Tests for the Python EtherNet/IP bindings."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from instro.unstable import _ethernetip as ethernetip
from tests.cpppo_sim_server import start_server_with_retries

EtherNetIpSession = ethernetip.EtherNetIpSession
PlcKind = ethernetip.PlcKind
PlcValue = ethernetip.PlcValue
StructuredValue = ethernetip.StructuredValue

SUPPORTED_CPPPO_SCALAR_CASES: list[dict[str, Any]] = [
    {
        "name": "bool_tag",
        "type_name": "BOOL",
        "initial": False,
        "expected_kind": PlcKind.BOOL,
        "write": True,
        "expected_after": True,
    },
    {
        "name": "sint_tag",
        "type_name": "SINT",
        "initial": -3,
        "expected_kind": PlcKind.SINT,
        "write": PlcValue.sint(-2),
        "expected_after": -2,
    },
    {
        "name": "int_tag",
        "type_name": "INT",
        "initial": -12,
        "expected_kind": PlcKind.INT,
        "write": PlcValue.int(-11),
        "expected_after": -11,
    },
    {
        "name": "dint_tag",
        "type_name": "DINT",
        "initial": 1234,
        "expected_kind": PlcKind.DINT,
        "write": PlcValue.dint(1235),
        "expected_after": 1235,
    },
    {
        "name": "lint_tag",
        "type_name": "LINT",
        "initial": -5678,
        "expected_kind": PlcKind.LINT,
        "write": PlcValue.lint(-5677),
        "expected_after": -5677,
    },
    {
        "name": "usint_tag",
        "type_name": "USINT",
        "initial": 7,
        "expected_kind": PlcKind.USINT,
        "write": PlcValue.usint(8),
        "expected_after": 8,
    },
    {
        "name": "uint_tag",
        "type_name": "UINT",
        "initial": 42,
        "expected_kind": PlcKind.UINT,
        "write": PlcValue.uint(43),
        "expected_after": 43,
    },
    {
        "name": "udint_tag",
        "type_name": "UDINT",
        "initial": 99,
        "expected_kind": PlcKind.UDINT,
        "write": PlcValue.udint(100),
        "expected_after": 100,
    },
    {
        "name": "ulint_tag",
        "type_name": "ULINT",
        "initial": 123_456,
        "expected_kind": PlcKind.ULINT,
        "write": PlcValue.ulint(123_457),
        "expected_after": 123_457,
    },
    {
        "name": "real_tag",
        "type_name": "REAL",
        "initial": 1.25,
        "expected_kind": PlcKind.REAL,
        "write": PlcValue.real(2.5),
        "expected_after": pytest.approx(2.5),
    },
    {
        "name": "lreal_tag",
        "type_name": "LREAL",
        "initial": -9.5,
        "expected_kind": PlcKind.LREAL,
        "write": PlcValue.lreal(-8.25),
        "expected_after": pytest.approx(-8.25),
    },
]

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


@contextmanager
def cpppo_endpoint_for(tags: dict[str, tuple[str, Any]]) -> Iterator[str]:
    """Start a cpppo PLC on an ephemeral port and yield its endpoint."""
    pytest.importorskip("cpppo", reason="cpppo is required for the EtherNet/IP simulator test")
    server, port = start_server_with_retries(tags)
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.stop()


@pytest.fixture
def cpppo_endpoint() -> Iterator[str]:
    """Start a small cpppo PLC and yield its EtherNet/IP endpoint."""
    with cpppo_endpoint_for({"motor_enabled": ("BOOL", False), "speed_setpoint": ("DINT", 10)}) as endpoint:
        yield endpoint


def test_cpppo_round_trips_all_supported_scalar_types() -> None:
    """Cpppo round-trips every scalar kind it currently supports via explicit writes."""
    tags = {case["name"]: (case["type_name"], case["initial"]) for case in SUPPORTED_CPPPO_SCALAR_CASES}

    with cpppo_endpoint_for(tags) as endpoint:
        with EtherNetIpSession(endpoint) as session:
            results = session.read_tags([case["name"] for case in SUPPORTED_CPPPO_SCALAR_CASES])
            assert [name for name, _value in results] == [case["name"] for case in SUPPORTED_CPPPO_SCALAR_CASES]

            for case, (_name, value) in zip(SUPPORTED_CPPPO_SCALAR_CASES, results, strict=True):
                assert value.kind == case["expected_kind"]
                assert value.value == case["initial"]

            for case in SUPPORTED_CPPPO_SCALAR_CASES:
                session.write_tag(case["name"], case["write"])
                value = session.read_tag(case["name"])
                assert value.kind == case["expected_kind"]
                assert value.value == case["expected_after"]


@pytest.mark.xfail(
    reason="cpppo currently exposes STRING tags as unsupported type 0x00D0 to this EtherNet/IP client",
    strict=True,
)
def test_cpppo_string_tag_round_trip_is_not_currently_possible() -> None:
    """Flag the current cpppo STRING limitation so it stays visible in test output."""
    with cpppo_endpoint_for({"string_tag": ("STRING", "hello")}) as endpoint:
        with EtherNetIpSession(endpoint) as session:
            value = session.read_tag("string_tag")
            assert value.kind == PlcKind.STRING
            assert value.value == "hello"
            session.write_tag("string_tag", PlcValue.string("world"))
            after = session.read_tag("string_tag")
            assert after.kind == PlcKind.STRING
            assert after.value == "world"


def test_python_bindings_validate_numeric_and_bytes_boundaries(
    cpppo_endpoint: str,
) -> None:
    """The PyO3 bindings reject ambiguous write payloads before hitting the wire."""
    with EtherNetIpSession(cpppo_endpoint) as session:
        motor_enabled = session.read_tag("motor_enabled")
        speed_setpoint = session.read_tag("speed_setpoint")

        assert motor_enabled.kind == PlcKind.BOOL
        assert motor_enabled.value is False

        assert speed_setpoint.kind == PlcKind.DINT
        assert speed_setpoint.value == 10

        with pytest.raises(TypeError, match="PlcValue"):
            session.write_tag("speed_setpoint", 42)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="bytes and bytearray"):
            session.write_tag("speed_setpoint", b"\x01\x02")  # type: ignore[arg-type]

        session.write_tag("motor_enabled", True)
        session.write_tag("speed_setpoint", PlcValue.dint(42))

        tags = session.read_tags(["motor_enabled", "speed_setpoint"])
        assert [name for name, _value in tags] == ["motor_enabled", "speed_setpoint"]
        assert [value.kind for _name, value in tags] == [PlcKind.BOOL, PlcKind.DINT]
        assert [value.value for _name, value in tags] == [True, 42]
        assert session.closed is False

    assert session.closed is True
