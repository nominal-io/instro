from __future__ import annotations

from instro.unstable._ethernetip import EtherNetIpSession, PlcKind, PlcValue, StructuredValue

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
    session.write_tag("Tag", PlcValue.string("abc"))
    session.write_tag("Tag", PlcValue.structured(StructuredValue(data=b"abc")))
