from __future__ import annotations

from instro.unstable._ethernetip import EtherNetIpSession


def read_tags_rejects_bare_string(session: EtherNetIpSession) -> None:
    session.read_tags("Tag")
