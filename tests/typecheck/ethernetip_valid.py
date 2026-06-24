from __future__ import annotations

from instro.unstable._ethernetip import (
    CipError,
    DataTypeMismatchError,
    EtherNetIpBatchError,
    EtherNetIpError,
    EtherNetIpSession,
    PlcKind,
    PlcValue,
    StructuredValue,
    TagNotFoundError,
)

payload: bytes = bytes(StructuredValue(data=b"abc"))
kind: PlcKind = PlcValue.dint(1).kind
session_with_route = EtherNetIpSession("192.0.2.10:44818", route_path_slots=[0])


def write_supported_values(session: EtherNetIpSession) -> None:
    session.write_tag("Tag", StructuredValue(data=b"abc"))
    session.write_tag("Tag", PlcValue.bool(True))
    session.write_tag("Tag", PlcValue.sint(-1))
    session.write_tag("Tag", PlcValue.int(-1))
    session.write_tag("Tag", PlcValue.dint(-1))
    session.write_tag("Tag", PlcValue.lint(-1))
    session.write_tag("Tag", PlcValue.usint(1))
    session.write_tag("Tag", PlcValue.uint(1))
    session.write_tag("Tag", PlcValue.udint(1))
    session.write_tag("Tag", PlcValue.ulint(1))
    session.write_tag("Tag", PlcValue.real(1.0))
    session.write_tag("Tag", PlcValue.lreal(1.0))
    session.write_tag("Tag", PlcValue.structured(StructuredValue(data=b"abc")))


def read_tags_handles_per_tag_errors(session: EtherNetIpSession) -> None:
    for name, result in session.read_tags(["A", "B"]):
        if isinstance(result, EtherNetIpError):
            print(name, result)
        else:
            print(name, result.kind)


def read_tags_narrows_batch_error_variants(session: EtherNetIpSession) -> None:
    for name, result in session.read_tags(["A", "B"]):
        if isinstance(result, TagNotFoundError):
            print(name, "missing:", result.tag_name)
        elif isinstance(result, DataTypeMismatchError):
            print(name, "expected", result.expected, "got", result.actual)
        elif isinstance(result, CipError):
            print(name, "cip", result.status, result.message)
        elif isinstance(result, EtherNetIpBatchError):
            print(name, "other batch failure:", result)
        else:
            print(name, result.kind)
