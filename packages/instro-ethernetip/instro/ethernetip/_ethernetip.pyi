# Alias the builtin int so there's no confusion from PlcValue.int() shadowing
# the builtin type.
from builtins import int as _int
from typing import ClassVar, TypeAlias

class EtherNetIpError(Exception):
    addr: str | None
    tag_name: str | None
    operation: str | None


class EtherNetIpBatchError(EtherNetIpError):
    """Base class for per-tag failures returned by :meth:`EtherNetIpSession.read_tags`."""


class TagNotFoundError(EtherNetIpBatchError): ...


class DataTypeMismatchError(EtherNetIpBatchError):
    expected: str
    actual: str


class NetworkBatchError(EtherNetIpBatchError): ...


class CipError(EtherNetIpBatchError):
    status: _int
    message: str


class TagPathError(EtherNetIpBatchError): ...


class SerializationError(EtherNetIpBatchError): ...


class BatchTimeoutError(EtherNetIpBatchError): ...


class OtherBatchError(EtherNetIpBatchError): ...


class StructuredValue:
    def __init__(self, symbol_id: _int | None = None, data: bytes | bytearray | None = None) -> None: ...

    @property
    def symbol_id(self) -> _int | None: ...

    @property
    def data(self) -> bytes: ...

    def __bytes__(self) -> bytes: ...

    def __repr__(self) -> str: ...


class PlcKind:
    # Mirrors the discriminant of the Rust Value type wrapped by PlcValue.
    BOOL: ClassVar["PlcKind"]
    SINT: ClassVar["PlcKind"]
    INT: ClassVar["PlcKind"]
    DINT: ClassVar["PlcKind"]
    LINT: ClassVar["PlcKind"]
    USINT: ClassVar["PlcKind"]
    UINT: ClassVar["PlcKind"]
    UDINT: ClassVar["PlcKind"]
    ULINT: ClassVar["PlcKind"]
    REAL: ClassVar["PlcKind"]
    LREAL: ClassVar["PlcKind"]
    STRUCTURED: ClassVar["PlcKind"]


PlcPayload: TypeAlias = bool | _int | float | StructuredValue


class PlcValue:
    @staticmethod
    def bool(value: bool) -> "PlcValue": ...

    @staticmethod
    def sint(value: _int) -> "PlcValue": ...

    @staticmethod
    def int(value: _int) -> "PlcValue": ...

    @staticmethod
    def dint(value: _int) -> "PlcValue": ...

    @staticmethod
    def lint(value: _int) -> "PlcValue": ...

    @staticmethod
    def usint(value: _int) -> "PlcValue": ...

    @staticmethod
    def uint(value: _int) -> "PlcValue": ...

    @staticmethod
    def udint(value: _int) -> "PlcValue": ...

    @staticmethod
    def ulint(value: _int) -> "PlcValue": ...

    @staticmethod
    def real(value: float) -> "PlcValue": ...

    @staticmethod
    def lreal(value: float) -> "PlcValue": ...

    @staticmethod
    def structured(value: StructuredValue) -> "PlcValue": ...

    @property
    def kind(self) -> PlcKind: ...

    @property
    def value(self) -> PlcPayload: ...

    def __repr__(self) -> str: ...


class EtherNetIpSession:
    def __init__(
        self,
        address: str,
        route_path_slots: list[_int] | tuple[_int, ...] | None = None,
    ) -> None: ...

    @property
    def address(self) -> str: ...

    @property
    def closed(self) -> bool: ...

    def read_tag(self, name: str) -> PlcValue: ...

    def read_tags(
        self,
        names: list[str] | tuple[str, ...],
    ) -> list[tuple[str, PlcValue | EtherNetIpBatchError]]:
        """Read several PLC tags in a single batched request.

        Returns one entry per requested tag in input order. The second item of each tuple is
        either a ``PlcValue`` for successful reads or an ``EtherNetIpBatchError`` subclass
        instance for tags that failed individually. The concrete subclass preserves the
        upstream variant (e.g. ``TagNotFoundError``, ``DataTypeMismatchError``, ``CipError``)
        so callers can branch with ``isinstance`` rather than parsing strings.

        Per-tag failures are returned as exception instances rather than raised, so a single bad
        tag does not throw away the values of the other tags in the batch. This call raises only
        when the entire batch could not be dispatched.
        """
        ...

    def write_tag(self, name: str, value: PlcValue | StructuredValue | bool) -> None: ...

    def close(self) -> None: ...

    def __enter__(self) -> "EtherNetIpSession": ...

    def __exit__(self, exc_type: object | None, exc: BaseException | None, tb: object | None) -> bool: ...

    def __repr__(self) -> str: ...
