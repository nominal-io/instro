"""SDR interface package.

The SDR layer mirrors the repo's existing unstable instrument pattern: a generic
abstract driver contract defines the minimal hardware API, and a higher-level
``InstroSDR`` wrapper exposes measurement and configuration methods using the
shared ``Measurement``/``Command`` objects from the core library.
"""

from instro.unstable.sdr.sdr import InstroSDR, SDRDriverBase

__all__ = [
    "InstroSDR",
    "SDRDriverBase",
]
