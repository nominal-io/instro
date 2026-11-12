"""Every community-contributed driver in one namespace.

``from instro.contrib.drivers import MatrixWPS300S`` is the same class as
``from instro.contrib.psu.drivers import MatrixWPS300S``.
"""

from instro.contrib.psu.drivers import MatrixWPS300S

__all__ = ["MatrixWPS300S"]
