"""Shared EtherNet/IP native-module fakes for tests."""

from __future__ import annotations

from builtins import int as _int
from dataclasses import dataclass, field
from enum import Enum
from types import SimpleNamespace

import pytest

import instro.unstable.ethernetip.ethernetip as ethernetip_module


class FakePlcKind(Enum):
    BOOL = "BOOL"
    SINT = "SINT"
    INT = "INT"
    DINT = "DINT"
    LINT = "LINT"
    USINT = "USINT"
    UINT = "UINT"
    UDINT = "UDINT"
    ULINT = "ULINT"
    REAL = "REAL"
    LREAL = "LREAL"
    STRING = "STRING"
    STRUCTURED = "STRUCTURED"


@dataclass(frozen=True)
class FakePlcValue:
    kind: FakePlcKind
    value: object

    @staticmethod
    def bool(value: bool) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.BOOL, value)

    @staticmethod
    def sint(value: _int) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.SINT, value)

    @staticmethod
    def int(value: _int) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.INT, value)

    @staticmethod
    def dint(value: _int) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.DINT, value)

    @staticmethod
    def lint(value: _int) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.LINT, value)

    @staticmethod
    def usint(value: _int) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.USINT, value)

    @staticmethod
    def uint(value: _int) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.UINT, value)

    @staticmethod
    def udint(value: _int) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.UDINT, value)

    @staticmethod
    def ulint(value: _int) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.ULINT, value)

    @staticmethod
    def real(value: float) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.REAL, value)

    @staticmethod
    def lreal(value: float) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.LREAL, value)

    @staticmethod
    def string(value: str) -> "FakePlcValue":
        return FakePlcValue(FakePlcKind.STRING, value)


@dataclass
class FakeEtherNetIPNativeState:
    values: dict[str, FakePlcValue] = field(default_factory=dict)
    sessions: list[tuple[str, list[_int] | None]] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    writes: list[tuple[str, object]] = field(default_factory=list)


def install_fake_native_ethernetip(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, FakePlcValue] | None = None,
) -> FakeEtherNetIPNativeState:
    state = FakeEtherNetIPNativeState(values=values or {})

    class FakeSession:
        def __init__(self, address: str, route_path_slots: list[_int] | None = None):
            state.sessions.append((address, route_path_slots))

        def read_tag(self, name: str) -> FakePlcValue:
            state.reads.append(name)
            return state.values[name]

        def read_tags(self, names: list[str]) -> list[tuple[str, FakePlcValue]]:
            return [(name, state.values[name]) for name in names]

        def write_tag(self, name: str, value: object) -> None:
            state.writes.append((name, value))

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        ethernetip_module,
        "_load_native_ethernetip",
        lambda: SimpleNamespace(
            EtherNetIpSession=FakeSession,
            PlcKind=FakePlcKind,
            PlcValue=FakePlcValue,
        ),
    )

    return state
