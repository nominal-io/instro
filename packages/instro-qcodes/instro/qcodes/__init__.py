"""Publish QCoDeS instrument traffic through any instro publisher (Nominal, files, ...).

Wrap a QCoDeS instrument with `QcodesInstrument` (instro-first code) or `attach` (existing QCoDeS
code): every parameter set publishes a ``.cmd`` command channel and every parameter get publishes a
measurement channel. `ChannelMap` (or ``instro qcodes map``) controls the names.
"""

from instro.qcodes.channel_map import ChannelMap
from instro.qcodes.inspection import ChannelSpec, default_channel_name, describe
from instro.qcodes.wrapper import QcodesInstrument, attach

__all__ = [
    "ChannelMap",
    "ChannelSpec",
    "QcodesInstrument",
    "attach",
    "default_channel_name",
    "describe",
]
