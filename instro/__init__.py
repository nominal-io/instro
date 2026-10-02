"""Typed Python API for test-and-measurement instruments."""

# Let workspace packages (instro-contrib, instro-unstable, instro-ethernetip, ...)
# contribute top-level subpackages under instro.* — without this, turning instro
# into a regular package below would pin its __path__ to this directory alone
# and hide those workspace trees. The exact spelling below (rather than
# `from pkgutil import extend_path; ...`) matters: griffe/mkdocstrings pattern-match
# this specific idiom to keep treating instro as a mergeable namespace for API docs.
__path__ = __import__("pkgutil").extend_path(__path__, __name__)

from instro import cli, daq, dmm, eload, i2c, lib, modbus, psu, scope

__all__ = ["cli", "daq", "dmm", "eload", "i2c", "lib", "modbus", "psu", "scope"]
