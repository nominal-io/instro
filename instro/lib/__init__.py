"""Cross-category building blocks: base instrument, transports, publishers, shared types and config.

Everything public in the ``instro.lib`` subpackages imports from here::

    from instro.lib import BackgroundDaemonConfig, NominalCorePublisher, VisaConfig

The submodule paths (``instro.lib.publishers``, ``instro.lib.transports``, ``instro.lib.types``, ...) still
work and export the same objects.
"""

from instro.lib._display import DisplayOptions, get_display_options, set_display_options
from instro.lib.config import (
    FilePublisherConfig,
    NominalCorePublisherConfig,
    PublisherConfigType,
    TimingConfig,
    build_publisher,
    load_config,
)
from instro.lib.discover import (
    DiscoveredInstrument,
    DriverMatch,
    IdnFields,
    VisaInstrumentInfo,
    VisaScanError,
    VisaScanResult,
    VisaUnrecognizedInstrument,
    match_idn,
    parse_idn,
    scan_visa_resources,
)
from instro.lib.exceptions import FeatureNotSupportedError, InstroError, InstrumentNotOpenError
from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.nominal import install_nominal_core_log_handler
from instro.lib.publishers import (
    BasicBufferedPublisher,
    BufferedPublisher,
    FilePublisher,
    NominalConnectPublisher,
    NominalCorePublisher,
    PlotlyPublisher,
    Publisher,
    QueuedPublisher,
    SharedPublisher,
)
from instro.lib.registry import (
    CATEGORIES,
    DriverEntry,
    IdnPattern,
    check_registry,
    driver_registry,
    iter_driver_entries,
)
from instro.lib.transports import (
    ControlFlow,
    DataType,
    ModbusRTUTransport,
    ModbusTCPTransport,
    ModbusTransport,
    Parity,
    RegisterType,
    SerialConfig,
    StopBits,
    TerminatorConfig,
    TimeoutConfig,
    TransportBase,
    VisaConfig,
    VisaDriver,
)
from instro.lib.types import BackgroundDaemonConfig, Command, DeviceInfo, LinearScale, Measurement, ScaleType

__all__ = [
    # Notebook display
    "DisplayOptions",
    "get_display_options",
    "set_display_options",
    # Instrument base and errors
    "FeatureNotSupportedError",
    "InstroError",
    "Instrument",
    "InstrumentNotOpenError",
    "publish_command",
    "publish_measurement",
    # Data types
    "BackgroundDaemonConfig",
    "Command",
    "DeviceInfo",
    "LinearScale",
    "Measurement",
    "ScaleType",
    # Publishers
    "BasicBufferedPublisher",
    "BufferedPublisher",
    "FilePublisher",
    "NominalConnectPublisher",
    "NominalCorePublisher",
    "PlotlyPublisher",
    "Publisher",
    "QueuedPublisher",
    "SharedPublisher",
    "install_nominal_core_log_handler",
    # Transports
    "ControlFlow",
    "DataType",
    "ModbusRTUTransport",
    "ModbusTCPTransport",
    "ModbusTransport",
    "Parity",
    "RegisterType",
    "SerialConfig",
    "StopBits",
    "TerminatorConfig",
    "TimeoutConfig",
    "TransportBase",
    "VisaConfig",
    "VisaDriver",
    # JSON config
    "FilePublisherConfig",
    "NominalCorePublisherConfig",
    "PublisherConfigType",
    "TimingConfig",
    "build_publisher",
    "load_config",
    # Driver registry and discovery
    "CATEGORIES",
    "DiscoveredInstrument",
    "DriverEntry",
    "DriverMatch",
    "IdnFields",
    "IdnPattern",
    "VisaInstrumentInfo",
    "VisaScanError",
    "VisaScanResult",
    "VisaUnrecognizedInstrument",
    "check_registry",
    "driver_registry",
    "iter_driver_entries",
    "match_idn",
    "parse_idn",
    "scan_visa_resources",
]
