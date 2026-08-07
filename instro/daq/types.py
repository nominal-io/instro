"""DAQ shared types: vendors, channel types, terminal configs, hardware-timing config."""

from dataclasses import dataclass
from enum import Enum, IntEnum

from instro.daq.scaling.scaling import Scaler
from instro.daq.scaling.thermocouple import TC_TYPE, TC_UNIT


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


class CJCSource(Enum):
    INTERNAL = "INTERNAL"
    CONSTANT = "CONSTANT"
    CHANNEL = "CHANNEL"


@dataclass(frozen=True)
class HWTimingConfig:
    sample_rate: float
    sample_period: int
    samples_per_channel: int
    # sample_clock_source: str | None = None


@dataclass(frozen=True, kw_only=True)
class DAQChannel:
    physical_channel: str
    alias: str
    direction: Direction


# ========  Analog Channel Types  ===========


@dataclass(frozen=True)
class AnalogChannel(DAQChannel):
    range_max: float
    range_min: float
    scaler: Scaler | None
    terminal_config: TerminalConfig | None = None


@dataclass(frozen=True)
class AnalogVoltageChannel(DAQChannel):
    range_max: float
    range_min: float
    scaler: Scaler | None
    terminal_config: TerminalConfig | None = None


@dataclass(frozen=True)
class AnalogCurrentChannel(DAQChannel):
    range_max: float
    range_min: float
    scaler: Scaler | None


@dataclass(frozen=True)
class AnalogThermocoupleChannel(DAQChannel):
    range_max: float
    range_min: float
    scaler: Scaler | None
    tc_type: TC_TYPE
    cjc_source: CJCSource | None
    cjc_temp: float | None
    cjc_channel: str | None
    unit: TC_UNIT | None


# The analog channel types are siblings, so driver state that holds any of them is typed on this union.
AnalogChannelUnion = AnalogChannel | AnalogVoltageChannel | AnalogCurrentChannel | AnalogThermocoupleChannel


# ========  Digital Channel Types  ===========


class DigitalPortWidth(IntEnum):
    WIDTH_8 = 8
    WIDTH_16 = 16
    WIDTH_32 = 32
    WIDTH_64 = 64


@dataclass(frozen=True)
class DigitalChannel(DAQChannel):
    logic_level: float | None
    logic: Logic


@dataclass(frozen=True)
class DigitalPortChannel(DigitalChannel):
    width: DigitalPortWidth


@dataclass(frozen=True)
class DigitalLineChannel(DigitalChannel):
    bit_position: int | None = None


@dataclass(frozen=True)
class RelayChannel(DAQChannel):
    """A relay channel routed via open/close. ``direction`` is always ``OUTPUT`` (relay control is a command)."""

    pass
