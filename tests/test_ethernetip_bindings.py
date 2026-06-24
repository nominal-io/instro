"""Tests for the Python EtherNet/IP bindings."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from instro.unstable import _ethernetip as ethernetip
from tests.cpppo_sim_server import start_server_with_retries

PLC_ENDPOINT_ENV_VAR = "INSTRO_EIP_PLC_ENDPOINT"
ROUTE_PATH_SLOTS_ENV_VAR = "INSTRO_EIP_ROUTE_PATH_SLOTS"
TARGET_L32E_ENV_VAR = "INSTRO_EIP_TARGET_L32E"
EXCLUDE_TYPES_ENV_VAR = "INSTRO_EIP_EXCLUDE_TYPES"
EXCLUDE_UNSIGNED_TYPES_ENV_VAR = "INSTRO_EIP_EXCLUDE_UNSIGNED_TYPES"
UNSIGNED_TYPE_NAMES = {"USINT", "UINT", "UDINT", "ULINT"}

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

SUPPORTED_LIVE_PLC_SCALAR_CASES: list[dict[str, Any]] = [
    {
        "name": "test_bool",
        "type_name": "BOOL",
        "initial": False,
        "expected_kind": PlcKind.BOOL,
        "write": True,
        "expected_after": True,
    },
    {
        "name": "test_sint",
        "type_name": "SINT",
        "initial": -3,
        "expected_kind": PlcKind.SINT,
        "write": PlcValue.sint(-8),
        "expected_after": -8,
    },
    {
        "name": "test_int",
        "type_name": "INT",
        "initial": -12,
        "expected_kind": PlcKind.INT,
        "write": PlcValue.int(123),
        "expected_after": 123,
    },
    {
        "name": "test_dint",
        "type_name": "DINT",
        "initial": 10,
        "expected_kind": PlcKind.DINT,
        "write": PlcValue.dint(42),
        "expected_after": 42,
    },
    {
        "name": "test_lint",
        "type_name": "LINT",
        "initial": -5678,
        "expected_kind": PlcKind.LINT,
        "write": PlcValue.lint(987_654_321),
        "expected_after": 987_654_321,
    },
    {
        "name": "test_usint",
        "type_name": "USINT",
        "initial": 7,
        "expected_kind": PlcKind.USINT,
        "write": PlcValue.usint(9),
        "expected_after": 9,
    },
    {
        "name": "test_uint",
        "type_name": "UINT",
        "initial": 42,
        "expected_kind": PlcKind.UINT,
        "write": PlcValue.uint(128),
        "expected_after": 128,
    },
    {
        "name": "test_udint",
        "type_name": "UDINT",
        "initial": 99,
        "expected_kind": PlcKind.UDINT,
        "write": PlcValue.udint(456),
        "expected_after": 456,
    },
    {
        "name": "test_ulint",
        "type_name": "ULINT",
        "initial": 123_456,
        "expected_kind": PlcKind.ULINT,
        "write": PlcValue.ulint(987_654),
        "expected_after": 987_654,
    },
    {
        "name": "test_real",
        "type_name": "REAL",
        "initial": 1.25,
        "expected_kind": PlcKind.REAL,
        "write": PlcValue.real(3.5),
        "expected_after": pytest.approx(3.5),
    },
    {
        "name": "test_lreal",
        "type_name": "LREAL",
        "initial": -9.5,
        "expected_kind": PlcKind.LREAL,
        "write": PlcValue.lreal(6.25),
        "expected_after": pytest.approx(6.25),
    },
]


def cpppo_scalar_cases() -> list[dict[str, Any]]:
    """Scalar cases expected on the target endpoint for read/write integration tests."""
    return SUPPORTED_CPPPO_SCALAR_CASES


def live_plc_scalar_cases() -> list[dict[str, Any]]:
    """Scalar cases expected on the configured live PLC endpoint."""
    cases = SUPPORTED_LIVE_PLC_SCALAR_CASES

    if exclude_unsigned_types():
        cases = [case for case in cases if case["type_name"] not in UNSIGNED_TYPE_NAMES]

    excluded_types = excluded_type_names()
    if excluded_types:
        cases = [case for case in cases if case["type_name"] not in excluded_types]

    return cases


def excluded_type_names() -> set[str]:
    type_names: set[str] = set()

    value = os.getenv(EXCLUDE_TYPES_ENV_VAR)
    if value is not None:
        type_names.update(type_name.strip().upper() for type_name in value.split(",") if type_name.strip())

    if target_l32e():
        type_names.add("LREAL")

    return type_names


def exclude_unsigned_types() -> bool:
    return truthy_env(EXCLUDE_UNSIGNED_TYPES_ENV_VAR) or target_l32e()


def target_l32e() -> bool:
    return truthy_env(TARGET_L32E_ENV_VAR)


def truthy_env(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def route_path_slots() -> list[int] | None:
    value = os.getenv(ROUTE_PATH_SLOTS_ENV_VAR)
    if value is None:
        return None

    slots = [int(slot.strip()) for slot in value.split(",") if slot.strip()]
    for slot in slots:
        if slot < 0 or slot > 255:
            raise ValueError(f"{ROUTE_PATH_SLOTS_ENV_VAR} values must be between 0 and 255")
    return slots or None


def ethernetip_session(endpoint: str, *, use_configured_route_path: bool = False) -> EtherNetIpSession:
    slots = route_path_slots() if use_configured_route_path else None
    return EtherNetIpSession(endpoint, route_path_slots=slots)


def test_ethernetip_native_types_use_private_local_import_path() -> None:
    """Native EtherNet/IP bindings should stay private until the published wheel surface exists."""
    assert EtherNetIpSession.__module__ == "instro.unstable._ethernetip"
    assert PlcKind.__module__ == "instro.unstable._ethernetip"
    assert StructuredValue.__module__ == "instro.unstable._ethernetip"
    assert importlib.util.find_spec("instro.ethernetip") is None


def test_plc_value_preserves_explicit_scalar_kinds() -> None:
    """`PlcValue` constructors preserve every supported scalar PLC kind."""
    assert not hasattr(PlcKind, "STRING")
    assert not hasattr(PlcValue, "string")

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
    ]

    for value, expected_kind, expected_payload in cases:
        assert value.kind == expected_kind
        assert value.value == expected_payload


def test_batch_error_subclasses_form_expected_hierarchy() -> None:
    """Per-variant batch errors are exposed as subclasses of EtherNetIpBatchError."""
    EtherNetIpError = ethernetip.EtherNetIpError
    EtherNetIpBatchError = ethernetip.EtherNetIpBatchError

    assert issubclass(EtherNetIpBatchError, EtherNetIpError)

    variant_classes = [
        ethernetip.TagNotFoundError,
        ethernetip.DataTypeMismatchError,
        ethernetip.NetworkBatchError,
        ethernetip.CipError,
        ethernetip.TagPathError,
        ethernetip.SerializationError,
        ethernetip.BatchTimeoutError,
        ethernetip.OtherBatchError,
    ]
    for cls in variant_classes:
        assert issubclass(cls, EtherNetIpBatchError)
        assert issubclass(cls, EtherNetIpError)
        assert cls.__module__ == "instro.unstable._ethernetip"


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


def live_plc_endpoint() -> str:
    """Resolve the explicitly configured live PLC endpoint for opt-in tests."""
    endpoint = os.getenv(PLC_ENDPOINT_ENV_VAR)
    if endpoint is None:
        pytest.skip(f"{PLC_ENDPOINT_ENV_VAR} is required for live PLC tests")

    endpoint = endpoint.strip()
    if not endpoint:
        raise ValueError(f"{PLC_ENDPOINT_ENV_VAR} must not be empty when set")
    return endpoint


@pytest.fixture
def cpppo_endpoint() -> Iterator[str]:
    """Start a small cpppo PLC and yield its EtherNet/IP endpoint."""
    with cpppo_endpoint_for({"motor_enabled": ("BOOL", False), "speed_setpoint": ("DINT", 10)}) as endpoint:
        yield endpoint


def test_cpppo_round_trips_all_supported_scalar_types() -> None:
    """Cpppo round-trips every scalar kind it currently supports via explicit writes."""
    cases = cpppo_scalar_cases()
    tags = {case["name"]: (case["type_name"], case["initial"]) for case in cases}

    with cpppo_endpoint_for(tags) as endpoint:
        assert_scalar_round_trip(endpoint, cases)


@pytest.mark.hardware
def test_live_plc_round_trips_configured_scalar_tags() -> None:
    """Live PLC round-trips the configured scalar test tags via the Python bindings."""
    # Hardware is the intended target, though the endpoint may be a simulator.
    assert_scalar_round_trip(
        live_plc_endpoint(),
        live_plc_scalar_cases(),
        use_configured_route_path=True,
    )


def assert_scalar_round_trip(
    endpoint: str,
    cases: list[dict[str, Any]],
    *,
    use_configured_route_path: bool = False,
) -> None:
    """Assert the same scalar write/read behavior for simulator and live PLC targets."""
    with ethernetip_session(endpoint, use_configured_route_path=use_configured_route_path) as session:
        try:
            for case in cases:
                session.write_tag(case["name"], plc_value_for_case(case, "initial"))

            results = session.read_tags([case["name"] for case in cases])
            assert [name for name, _value in results] == [case["name"] for case in cases]

            for case, (_name, value) in zip(cases, results, strict=True):
                assert value.kind == case["expected_kind"]
                assert value.value == case["initial"]

            for case in cases:
                session.write_tag(case["name"], case["write"])
                value = session.read_tag(case["name"])
                assert value.kind == case["expected_kind"]
                assert value.value == case["expected_after"]
        finally:
            for case in cases:
                session.write_tag(case["name"], plc_value_for_case(case, "initial"))


def plc_value_for_case(case: dict[str, Any], value_key: str) -> PlcValue:
    """Build a typed write value for a scalar cpppo fixture case."""
    constructor = getattr(PlcValue, case["type_name"].lower())
    return constructor(case[value_key])


@pytest.mark.xfail(
    reason="cpppo currently exposes STRING tags as unsupported type 0x00D0 to this EtherNet/IP client",
    strict=True,
)
def test_cpppo_string_tag_round_trip_is_not_currently_possible() -> None:
    """Flag the current cpppo STRING limitation so it stays visible in test output."""
    with cpppo_endpoint_for({"string_tag": ("STRING", "hello")}) as endpoint:
        with ethernetip_session(endpoint) as session:
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
    with ethernetip_session(cpppo_endpoint) as session:
        session.write_tag("motor_enabled", PlcValue.bool(False))
        session.write_tag("speed_setpoint", PlcValue.dint(10))

        try:
            motor_enabled = session.read_tag("motor_enabled")
            speed_setpoint = session.read_tag("speed_setpoint")

            assert motor_enabled.kind == PlcKind.BOOL
            assert motor_enabled.value is False

            assert speed_setpoint.kind == PlcKind.DINT
            assert speed_setpoint.value == 10

            with pytest.raises(TypeError, match="PlcValue"):
                session.write_tag("speed_setpoint", 42)  # type: ignore[arg-type]

            with pytest.raises(TypeError, match="PlcValue, StructuredValue, or bool"):
                session.write_tag("speed_setpoint", "startup")  # type: ignore[arg-type]

            with pytest.raises(TypeError, match="bytes and bytearray"):
                session.write_tag("speed_setpoint", b"\x01\x02")  # type: ignore[arg-type]

            session.write_tag("motor_enabled", True)
            session.write_tag("speed_setpoint", PlcValue.dint(42))

            tags = session.read_tags(["motor_enabled", "speed_setpoint"])
            assert [name for name, _value in tags] == ["motor_enabled", "speed_setpoint"]
            assert [value.kind for _name, value in tags] == [PlcKind.BOOL, PlcKind.DINT]
            assert [value.value for _name, value in tags] == [True, 42]
            assert session.closed is False
        finally:
            session.write_tag("motor_enabled", PlcValue.bool(False))
            session.write_tag("speed_setpoint", PlcValue.dint(10))

    assert session.closed is True
