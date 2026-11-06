"""Shared tools for every instrument: publishers, transports, data types, and errors.

Publishers send each reading and command to a destination; pass them to any instrument::

    from instro import InstroPSU
    from instro.drivers import VirtualPSU
    from instro.lib import FilePublisher, NominalCorePublisher

    publishers = [FilePublisher(format="jsonl", directory="/tmp/instro/"), NominalCorePublisher("<dataset_rid>")]
    psu = InstroPSU(name="myPSU", driver=VirtualPSU(num_channels=1), num_channels=1, publishers=publishers)

What's here:

Publishers
    ``NominalCorePublisher``, ``NominalConnectPublisher``, ``FilePublisher``, ``PlotlyPublisher``, and the
    ``Publisher`` protocol for writing your own.
Transports
    ``VisaConfig`` and ``SerialConfig`` to customize how a driver connects; ``ModbusTCPTransport`` and
    ``ModbusRTUTransport`` to share one Modbus connection between devices.
Data
    ``Measurement`` and ``Command``, the records instruments return and publish.
Errors
    ``InstroError`` and its subclasses, such as ``FeatureNotSupportedError``.
Discovery
    ``scan_visa_resources`` and ``match_idn`` find connected instruments and the drivers that support them.
"""

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
    # Logging
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
