"""Publishers that deliver Measurement/Command data to destinations (Nominal Core/Connect, files, buffers)."""

from typing import TYPE_CHECKING, Any

from instro.lib.publishers.files import FilePublisher
from instro.lib.publishers.nominal_connect import NominalConnectPublisher
from instro.lib.publishers.plot import PlotlyPublisher
from instro.lib.publishers.publisher import (
    BasicBufferedPublisher,
    BufferedPublisher,
    Publisher,
    QueuedPublisher,
    SharedPublisher,
)

if TYPE_CHECKING:
    from instro.lib.publishers.nominal_core import NominalCorePublisher


# Loaded on first access so importing instro does not require the `nominal` package (#626).
def __getattr__(name: str) -> Any:
    if name == "NominalCorePublisher":
        from instro.lib.publishers.nominal_core import NominalCorePublisher

        return NominalCorePublisher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FilePublisher",
    "NominalConnectPublisher",
    "NominalCorePublisher",
    "PlotlyPublisher",
    "Publisher",
    "BufferedPublisher",
    "BasicBufferedPublisher",
    "QueuedPublisher",
    "SharedPublisher",
]
