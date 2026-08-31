"""JSON config schema and factory for constructing InstroAWG from a JSON/dict config."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationInfo, field_validator, model_validator

from instro.lib.config import (
    FilePublisherConfig,
    NominalCorePublisherConfig,
    PublisherConfigType,
    TimingConfig,
    build_publisher,
)
from instro.lib.transports.visa import VisaConfig
from instro.lib.types import DeviceInfo
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    Triangle,
    Waveform,
)

if TYPE_CHECKING:
    from instro.lib.publishers import Publisher
    from instro.unstable.awg.awg import AWGDriverBase

__all__ = [
    "AWGConfig",
    "AmplitudeConfig",
    "ArbitraryConfig",
    "ChannelConfig",
    "DeviceInfo",
    "FilePublisherConfig",
    "NominalCorePublisherConfig",
    "PulseConfig",
    "SawtoothConfig",
    "SineConfig",
    "SquareConfig",
    "StaticValueConfig",
    "TimingConfig",
    "TriangleConfig",
    "VisaDriverConfig",
    "WaveformConfigType",
    "build_waveform",
    "resolve_awg_from_config",
]

AWG_VENDOR_REGISTRY: dict[str, str] = {
    "Keysight33521B": "instro.unstable.awg.drivers.keysight_33521b.Keysight33521B",
    "RigolDG1022Z": "instro.unstable.awg.drivers.rigol_dg1022z.RigolDG1022Z",
}


class VisaDriverConfig(BaseModel):
    """Driver config for a VISA-connected AWG."""

    model_config = ConfigDict(extra="forbid")
    connection_type: Literal["visa"] = "visa"
    name: str = Field(description="AWG vendor/model key.")
    num_channels: int = Field(ge=1, description="Number of output channels.")
    visa: VisaConfig

    @field_validator("name")
    @classmethod
    def name_must_be_registered(cls, v: str) -> str:
        if v not in AWG_VENDOR_REGISTRY:
            raise ValueError(f"unknown driver {v!r}")
        return v


class SineConfig(BaseModel):
    """Sine waveform config."""

    model_config = ConfigDict(extra="forbid")
    shape: Literal["sine"] = "sine"
    frequency_hz: float
    phase_deg: float


class SquareConfig(BaseModel):
    """Square waveform config."""

    model_config = ConfigDict(extra="forbid")
    shape: Literal["square"] = "square"
    frequency_hz: float
    duty_cycle_pct: float
    phase_deg: float


class SawtoothConfig(BaseModel):
    """Sawtooth waveform config."""

    model_config = ConfigDict(extra="forbid")
    shape: Literal["sawtooth"] = "sawtooth"
    frequency_hz: float
    phase_deg: float


class TriangleConfig(BaseModel):
    """Triangle waveform config."""

    model_config = ConfigDict(extra="forbid")
    shape: Literal["triangle"] = "triangle"
    frequency_hz: float
    phase_deg: float


class PulseConfig(BaseModel):
    """Pulse waveform config."""

    model_config = ConfigDict(extra="forbid")
    shape: Literal["pulse"] = "pulse"
    frequency_hz: float
    width_s: float
    delay_s: float


class ArbitraryConfig(BaseModel):
    """Arbitrary waveform config; samples are either given inline or read from a CSV file path."""

    model_config = ConfigDict(extra="forbid")
    shape: Literal["arbitrary"] = "arbitrary"
    samples: tuple[float, ...] | str = Field(
        description="At least 2 sample values normalized to [-1.0, 1.0], or a path to a CSV file containing "
        "them. A relative path resolves against the directory of the config file it came from."
    )
    sample_rate_sas: float = Field(description="Sample rate in samples per second.")

    _samples_path: Path | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _locate_samples_file(self, info: ValidationInfo) -> ArbitraryConfig:
        """Require a samples path to exist, so a bad path fails here rather than partway through ``open()``."""
        if not isinstance(self.samples, str):
            return self
        path = Path(self.samples)
        if not path.is_absolute():
            base_dir = (info.context or {}).get("config_dir")
            path = (Path(base_dir) if base_dir is not None else Path.cwd()) / path
        if not path.is_file():
            raise ValueError(f"arbitrary samples file not found: {path}")
        self._samples_path = path
        return self


class StaticValueConfig(BaseModel):
    """Static (DC) value config."""

    model_config = ConfigDict(extra="forbid")
    shape: Literal["static_value"] = "static_value"
    value: float


WaveformConfigType = Annotated[
    SineConfig | SquareConfig | SawtoothConfig | TriangleConfig | PulseConfig | ArbitraryConfig | StaticValueConfig,
    Field(discriminator="shape"),
]


def _parse_arbitrary_samples(config: ArbitraryConfig) -> tuple[float, ...]:
    """Return inline ``samples`` as-is, or read and flatten the CSV file they name."""
    if not isinstance(config.samples, str):
        return config.samples
    assert config._samples_path is not None
    with open(config._samples_path, newline="") as f:
        try:
            return tuple(float(value) for row in csv.reader(f) for value in row if value.strip())
        except ValueError as e:
            raise ValueError(f"{config._samples_path}: {e}") from e


def build_waveform(config: WaveformConfigType) -> Waveform:
    """Construct the runtime Waveform definition described by a waveform config block."""
    match config:
        case SineConfig():
            return Sine(frequency_hz=config.frequency_hz, phase_deg=config.phase_deg)
        case SquareConfig():
            return Square(
                frequency_hz=config.frequency_hz, duty_cycle_pct=config.duty_cycle_pct, phase_deg=config.phase_deg
            )
        case SawtoothConfig():
            return Sawtooth(frequency_hz=config.frequency_hz, phase_deg=config.phase_deg)
        case TriangleConfig():
            return Triangle(frequency_hz=config.frequency_hz, phase_deg=config.phase_deg)
        case PulseConfig():
            return Pulse(frequency_hz=config.frequency_hz, width_s=config.width_s, delay_s=config.delay_s)
        case ArbitraryConfig():
            return Arbitrary(samples=_parse_arbitrary_samples(config), sample_rate_hz=config.sample_rate_sas)
        case StaticValueConfig():
            return StaticValue(value=config.value)
        case _:
            raise AssertionError(f"unhandled waveform config {type(config).__name__}")


class AmplitudeConfig(BaseModel):
    """Output amplitude config; maps to ``InstroAWG.set_amplitude``."""

    model_config = ConfigDict(extra="forbid")
    value: float
    unit: AmplitudeMeasurementUnit = AmplitudeMeasurementUnit.VPP


class ChannelConfig(BaseModel):
    """Initial per-channel state, applied through the InstroAWG setters on ``open()``.

    ``output_enable`` cannot be set here: a configured channel stays silent until its
    output is explicitly enabled.
    """

    model_config = ConfigDict(extra="forbid")
    waveform: WaveformConfigType
    amplitude: AmplitudeConfig | None = None
    offset: float | None = None

    @model_validator(mode="after")
    def _validate_waveform(self) -> ChannelConfig:
        """Build the waveform now so ``types.py``'s shape-parameter bounds reject a bad config here, not mid-``open()``."""
        build_waveform(self.waveform)
        return self


class AWGConfig(BaseModel):
    """Validated config for constructing an InstroAWG from JSON."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    instrument: Literal["InstroAWG"] = "InstroAWG"
    device: DeviceInfo
    driver: VisaDriverConfig
    channels: dict[str, ChannelConfig] = Field(
        min_length=1, description="Per-channel config, keyed by 1-indexed channel number."
    )
    timing: TimingConfig | None = None
    publishers: list[PublisherConfigType] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_channel_keys(self) -> AWGConfig:
        seen: dict[int, str] = {}
        for key in self.channels:
            try:
                channel_number = int(key)
            except ValueError as e:
                raise ValueError(f"channel key {key!r} is not a valid channel number") from e
            if not 1 <= channel_number <= self.driver.num_channels:
                raise ValueError(
                    f"channel key {key!r} is out of range for a {self.driver.num_channels}-channel AWG "
                    f"(1-{self.driver.num_channels})"
                )
            if channel_number in seen:
                raise ValueError(
                    f"channels contains duplicate channel number {channel_number} "
                    f"(keys {seen[channel_number]!r} and {key!r})"
                )
            seen[channel_number] = key
        return self


def resolve_awg_from_config(
    config: AWGConfig,
) -> tuple[str, AWGDriverBase, int, list[Publisher], float | None]:
    """Resolve a validated AWGConfig into the ``(name, driver, num_channels, config_publishers, poll_interval)`` InstroAWG needs."""
    import importlib

    module_path, class_name = AWG_VENDOR_REGISTRY[config.driver.name].rsplit(".", 1)
    driver_cls = getattr(importlib.import_module(module_path), class_name)
    driver: AWGDriverBase = driver_cls(config.driver.visa)  # type: ignore[call-arg]

    config_publishers = [build_publisher(p) for p in config.publishers]
    poll_interval = config.timing.poll_interval if config.timing is not None else None
    return config.device.name, driver, config.driver.num_channels, config_publishers, poll_interval
