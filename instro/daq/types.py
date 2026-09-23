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
    # Unit of cjc_temp, range_min/range_max, and the temperatures the driver returns on read.
    unit: TC_UNIT
    # Volts-domain scaler applied before temperature conversion (LabJack only), e.g. descaling an amplified read from an LJTick-InAmp.
    tc_input_scaler: Scaler | None = None


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


# ========  Counter Output Channel Types  ===========


@dataclass(frozen=True)
class FrequencyPulseConfig:
    """Define a pulse with frequency and duty cycle."""

    frequency: float
    duty_cycle: float


@dataclass(frozen=True)
class TimingPulseConfig:
    """Define a pulse with high and low time (in seconds)."""

    high_time_s: float
    low_time_s: float


PulseConfig = FrequencyPulseConfig | TimingPulseConfig


@dataclass(frozen=True)
class CounterOutputChannel(DAQChannel):
    """A pulse train: continuous until stopped, or finite and self-stopping after ``n_pulses``."""

    pulse_config: PulseConfig
    idle_state: Logic = Logic.LOW
    # Counter that generates the train, e.g. "Dev1/ctr0"; ``physical_channel`` is the terminal it leaves by. Required on NI.
    counter_source: str | None = None
    continuous: bool = True
    # Required when ``continuous`` is False; ignored otherwise.
    n_pulses: int | None = None


# ========  Counter Input Channel Types  ===========


class CounterMeasurement(Enum):
    PULSE_COUNT = "PULSE_COUNT"  # counts
    FREQUENCY = "FREQUENCY"  # Hz
    PERIOD = "PERIOD"  # seconds
    PULSE_WIDTH = "PULSE_WIDTH"  # seconds


class Edge(Enum):
    RISING = "RISING"
    FALLING = "FALLING"


@dataclass(frozen=True)
class CounterInputChannel(DAQChannel):
    edge_type: Edge
    # Unit of the value read back: counts, Hz, or seconds.
    measurement: CounterMeasurement
    # Counter that takes the measurement, e.g. "Dev1/ctr0"; ``physical_channel`` is the terminal the signal arrives on.
    # Required on NI, where a counter routes to a PFI terminal; coupled to the terminal on MCC/LabJack.
    counter_source: str | None = None
    # PULSE_COUNT only.
    count_up: bool = True
    # Expected range of the measured value, in the measurement's own unit. Edge counting has no range,
    # and None leaves whatever range the vendor defaults to.
    range_min: float | None = None
    range_max: float | None = None


# ========  Relay Channel Types  ===========


@dataclass(frozen=True)
class RelayChannel(DAQChannel):
    """A relay channel routed via open/close. ``direction`` is always ``OUTPUT`` (relay control is a command)."""

    pass
