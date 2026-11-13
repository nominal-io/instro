"""Publish pymeasure instrument traffic through any instro publisher (Nominal, files, ...).

Wrap a pymeasure instrument with `PymeasureInstrument` (instro-first code) or `attach` (existing
pymeasure code): every property write publishes a ``.cmd`` command channel and every property read
publishes a measurement channel. `ChannelMap` (or ``instro pymeasure map``) controls the names.
"""

from instro.pymeasure.channel_map import ChannelMap
from instro.pymeasure.inspection import ChannelSpec, default_channel_name, describe
from instro.pymeasure.wrapper import PymeasureInstrument, attach

__all__ = [
    "ChannelMap",
    "ChannelSpec",
    "PymeasureInstrument",
    "attach",
    "default_channel_name",
    "describe",
]
