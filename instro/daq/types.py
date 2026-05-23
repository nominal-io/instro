"""DAQ shared types: vendors, channel types, terminal configs, hardware-timing config."""

from dataclasses import dataclass
from enum import Enum, IntEnum

from instro.daq.scaling.scaling import Scaler


class DAQVendor(Enum):
    NI = "NI DAQmx"
    LABJACK_T_SERIES = "LabJack T-Series"
    KEYSIGHT_34980 = "KEYSIGHT_34980"
    MCC = "MCC DAQ"
    # Add other vendors as needed


# TODO: kill this?
class ChannelType(Enum):
    ANALOG_INPUT = "ai"
    ANALOG_OUTPUT = "ao"
    DIGITAL_INPUT = "di"
    DIGITAL_OUTPUT = "do"


class Logic(Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class Direction(Enum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


class TerminalConfig(Enum):
    DIFF = "DIFFERENTIAL"
    NRSE = "NRSE"
    RSE = "RSE"


@dataclass
class HWTimingConfig:
    sample_rate: float
    sample_period: int
    samples_per_channel: int
    # sample_clock_source: str | None = None


@dataclass(kw_only=True)
class DAQChannel:
    physical_channel: str
    alias: str
    direction: Direction


@dataclass
class AnalogChannel(DAQChannel):
    range_max: float
    range_min: float
    scaler: Scaler | None
    terminal_config: TerminalConfig | None = None


class DigitalPortWidth(IntEnum):
    WIDTH_8 = 8
    WIDTH_16 = 16
    WIDTH_32 = 32
    WIDTH_64 = 64


@dataclass
class DigitalChannel(DAQChannel):
    logic_level: float | None
    logic: Logic


@dataclass
class DigitalPortChannel(DigitalChannel):
    width: DigitalPortWidth


@dataclass
class DigitalLineChannel(DigitalChannel):
    bit_position: int | None = None


@dataclass
class RelayChannel(DAQChannel):
    """A relay channel routed via open/close. ``direction`` is always ``OUTPUT`` (relay control is a command)."""

    pass
