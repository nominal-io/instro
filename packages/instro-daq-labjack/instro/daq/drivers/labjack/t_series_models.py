import logging
from typing import Protocol

from instro.daq.scaling.scaling import ReverseLinearScaler, Scaler
from instro.daq.types import (
    AnalogChannel,
    AnalogChannelUnion,
    AnalogThermocoupleChannel,
    AnalogVoltageChannel,
    DAQChannel,
    HWTimingConfig,
    TerminalConfig,
)
from labjack import ljm

logger = logging.getLogger(__name__)


class LJ_Model(Protocol):
    MIN_SCAN_RATE: float
    MAX_SCAN_RATE: float
    default_tc_input_scaler: Scaler | None

    def ai_channel_configs(
        self,
        channel: AnalogChannel | AnalogVoltageChannel,
    ) -> tuple[list[str], list[float] | list[int]]: ...

    def thermocouple_channel_configs(
        self,
        channel: AnalogThermocoupleChannel,
    ) -> tuple[list[str], list[float] | list[int]]: ...

    def tc_cjc_read_name(self, physical_channel: str) -> str | None: ...

    def refresh_tc_cjc(self, handle: int | None) -> None: ...

    def tc_cjc_kelvin(self, physical_channel: str, cjc_samples: dict[str, list[float]]) -> float: ...

    def hw_timing_configs(
        self,
        hw_timing_config: HWTimingConfig,
        channels: list[AnalogChannelUnion],
        stream_buffer_bytes: int = 0,
    ) -> tuple[list[str], list[float] | list[int]]: ...


class LJ_T4:
    """LabJack T4 device model constants."""

    AI_CHANNEL_PREFIX = "AIN"
    AO_CHANNEL_PREFIX = "DAC"
    VALID_RANGES = [10]
    MIN_SCAN_RATE = 0.0157
    MAX_SCAN_RATE = 50000.0
    # T4 thermocouples require an LJTick-InAmp; default to its x51 gain / 1.25 V offset jumpers.
    default_tc_input_scaler: Scaler | None = ReverseLinearScaler(gain=51, offset=1.25, units="V")

    def __init__(self):
        self._cjc_k: float | None = None

    def ai_channel_configs(
        self,
        channel: AnalogChannel | AnalogVoltageChannel,
    ) -> tuple[list[str], list[float] | list[int]]:
        """T4 AI channel config (RSE only; AIN# format)."""
        if not (channel.physical_channel.startswith(self.AI_CHANNEL_PREFIX) and channel.physical_channel[3:].isdigit()):
            raise ValueError(
                f"Channel '{channel}' must be in the format '{self.AI_CHANNEL_PREFIX}#' where # is an integer"
            )

        if channel.terminal_config and channel.terminal_config != TerminalConfig.RSE:
            raise ValueError(
                f"LabJack T4 only supports referenced single-ended mode, but {channel.terminal_config} was provided."
            )

        return self._ai_channel_configs(channel)

    def _ai_channel_configs(
        self, channel: AnalogChannel | AnalogVoltageChannel
    ) -> tuple[list[str], list[float] | list[int]]:
        """T4 has no per-channel AI config; returns empty names/values."""
        aNames = []  # type: ignore
        aValues = []  # type: ignore

        return aNames, aValues

    def thermocouple_channel_configs(
        self,
        channel: AnalogThermocoupleChannel,
    ) -> tuple[list[str], list[float] | list[int]]:
        """T4 thermocouple AI config: no per-channel registers; fixed ranges only."""
        if not (channel.physical_channel.startswith(self.AI_CHANNEL_PREFIX) and channel.physical_channel[3:].isdigit()):
            raise ValueError(
                f"Channel '{channel}' must be in the format '{self.AI_CHANNEL_PREFIX}#' where # is an integer"
            )

        logger.warning(
            "LabJack T4's 12-bit ADC cannot resolve a bare thermocouple; an LJTick-InAmp is assumed, and its "
            "gain/offset is backed out per the channel's tc_input_scaler (default: x51 gain, 1.25 V offset)."
        )

        return [], []

    def tc_cjc_read_name(self, physical_channel: str) -> str | None:
        """T4 has no streamable CJC source; CJC comes from the snapshot taken by ``refresh_tc_cjc``.

        NOTE: Because this value is only retrieved before streaming starts, it may drift over the course of a continuous stream.
        """
        return None

    def refresh_tc_cjc(self, handle: int | None) -> None:
        """Snapshot TEMPERATURE_DEVICE_K; Device temp (CJC) isn't streamable."""
        self._cjc_k = ljm.eReadName(handle, "TEMPERATURE_DEVICE_K")

    def tc_cjc_kelvin(self, physical_channel: str, cjc_samples: dict[str, list[float]]) -> float:
        """Last snapshot; during a stream that is start() time, so CJC can drift over a long session."""
        assert self._cjc_k is not None
        return self._cjc_k

    def hw_timing_configs(
        self,
        hw_timing_config: HWTimingConfig,
        channels: list[AnalogChannelUnion],
        stream_buffer_bytes: int = 0,
    ) -> tuple[list[str], list[float] | list[int]]:
        # Stream settling is 0 (default) and
        # stream resolution index is 0 (default).

        aNames = ["STREAM_SETTLING_US", "STREAM_RESOLUTION_INDEX", "STREAM_BUFFER_SIZE_BYTES"]
        aValues = [0, 0, stream_buffer_bytes]

        return aNames, aValues


class LJ_T7:
    """LabJack T7 device model constants."""

    AI_CHANNEL_PREFIX = "AIN"
    AO_CHANNEL_PREFIX = "DAC"
    VALID_RANGES = [10.0, 1.0, 0.1, 0.01]
    MIN_SCAN_RATE = 0.0157
    MAX_SCAN_RATE = 100000.0
    TC_RANGE = 0.1
    default_tc_input_scaler: Scaler | None = None

    def ai_channel_configs(
        self,
        channel: AnalogChannel | AnalogVoltageChannel,
    ) -> tuple[list[str], list[float] | list[int]]:
        if not (channel.physical_channel.startswith(self.AI_CHANNEL_PREFIX) and channel.physical_channel[3:].isdigit()):
            raise ValueError(
                f"Channel '{channel}' must be in the format '{self.AI_CHANNEL_PREFIX}#' where # is an integer"
            )

        return self._ai_channel_configs(channel)

    @staticmethod
    def _get_negative_channel(terminal_config: TerminalConfig | None, physical_channel: str) -> int:
        """Negative-channel register for ``terminal_config``. In ``DIFF``, the negative channel is ``+1`` (per LabJack T7 docs).

        See https://support.labjack.com/docs/14-3-0-analog-inputs-t7-t-series-datasheet#id-14.3.0AnalogInputs-T7[T-SeriesDatasheet]-Single-endedorDifferential
        """
        match terminal_config:
            case None:
                return 199
            case TerminalConfig.DIFF:
                return int(physical_channel[3:]) + 1
            case TerminalConfig.NRSE:
                raise ValueError("LabJack T7 does not support non-referenced single-ended mode.")
            case TerminalConfig.RSE:
                return 199
            case _:
                raise ValueError(
                    f"Invalid terminal configuration: {terminal_config}, must be one of {[cfg.name for cfg in TerminalConfig]}"
                )

    def _ai_channel_configs(
        self,
        channel: AnalogChannel | AnalogVoltageChannel,
    ) -> tuple[list[str], list[float] | list[int]]:
        range = self._compute_range(channel.range_min, channel.range_max)

        aNames = [f"{channel.physical_channel}_RANGE"]
        aValues = [range]

        # only write to negative channel if configured channel is even and less than 13
        if int(channel.physical_channel[3:]) % 2 == 0 and int(channel.physical_channel[3:]) < 13:
            negative_ch = self._get_negative_channel(channel.terminal_config, channel.physical_channel)
            aNames.append(f"{channel.physical_channel}_NEGATIVE_CH")
            aValues.append(negative_ch)

        return aNames, aValues

    def thermocouple_channel_configs(
        self,
        channel: AnalogThermocoupleChannel,
    ) -> tuple[list[str], list[float] | list[int]]:
        """T7 thermocouple AI config: ±0.1 V range, single-ended (TC- wired to GND)."""
        if not (channel.physical_channel.startswith(self.AI_CHANNEL_PREFIX) and channel.physical_channel[3:].isdigit()):
            raise ValueError(
                f"Channel '{channel}' must be in the format '{self.AI_CHANNEL_PREFIX}#' where # is an integer"
            )

        aNames: list[str] = [f"{channel.physical_channel}_RANGE"]
        aValues: list[float] = [self.TC_RANGE]

        # only write to negative channel if configured channel is even and less than 13
        if int(channel.physical_channel[3:]) % 2 == 0 and int(channel.physical_channel[3:]) < 13:
            aNames.append(f"{channel.physical_channel}_NEGATIVE_CH")
            aValues.append(self._get_negative_channel(None, channel.physical_channel))

        return aNames, aValues

    def tc_cjc_read_name(self, physical_channel: str) -> str:
        """AIN14 is the internal temp sensor's raw volts; streamable, unlike TEMPERATURE_DEVICE_K."""
        return "AIN14"

    def refresh_tc_cjc(self, handle: int | None) -> None:
        """No-op; CJC is streamable."""

    def tc_cjc_kelvin(self, physical_channel: str, cjc_samples: dict[str, list[float]]) -> float:
        """AIN14 volts → Kelvin (datasheet §18.0), minus 3 K to reflect screw-terminal temperature."""
        # CJC moves slowly; the batch's most recent sample is current enough.
        return cjc_samples[self.tc_cjc_read_name(physical_channel)][-1] * -92.6 + 467.6 - 3.0

    def _compute_range(self, range_min: float, range_max: float) -> float:
        abs_range_max = max(abs(range_min), abs(range_max))

        valid_ranges = (r for r in self.VALID_RANGES if r >= abs_range_max)
        if valid_ranges is None:
            raise ValueError(
                f"No valid range found in {self.VALID_RANGES} for requested range_min={range_min}, range_max={range_max}"
            )

        return min(valid_ranges)

    def hw_timing_configs(
        self,
        hw_timing_config: HWTimingConfig,
        channels: list[AnalogChannelUnion],
        stream_buffer_bytes: int = 0,
    ) -> tuple[list[str], list[float] | list[int]]:
        # I believe the Labjack only has one timing engine so no need to track channel_type

        # Ensure triggered stream is disabled.
        # Enabling internally-clocked stream.
        # Stream resolution index is 0 (default).
        # Mux Settling time = 0 (auto, driver configured based on sample rate)
        aNames = [
            "STREAM_TRIGGER_INDEX",
            "STREAM_CLOCK_SOURCE",
            "STREAM_RESOLUTION_INDEX",
            "STREAM_SETTLING_US",
            "STREAM_BUFFER_SIZE_BYTES",
        ]
        aValues = [0, 0, 0, 0, stream_buffer_bytes]

        return aNames, aValues


class LJ_T8:
    """LabJack T8 device model constants."""

    AI_CHANNEL_PREFIX = "AIN"
    AO_CHANNEL_PREFIX = "DAC"
    VALID_RANGES = [11.0, 9.6, 4.8, 2.4, 1.2, 0.6, 0.3, 0.15, 0.075, 0.036, 0.015]
    MIN_SCAN_RATE = 20.0
    MAX_SCAN_RATE = 40000.0
    TC_RANGE = 0.075
    default_tc_input_scaler: Scaler | None = None

    def ai_channel_configs(
        self, channel: AnalogChannel | AnalogVoltageChannel
    ) -> tuple[list[str], list[float] | list[int]]:
        if not (channel.physical_channel.startswith(self.AI_CHANNEL_PREFIX) and channel.physical_channel[3:].isdigit()):
            raise ValueError(
                f"Channel '{channel.physical_channel}' must be in the format '{self.AI_CHANNEL_PREFIX}#' where # is an integer"
            )

        if channel.terminal_config and channel.terminal_config != TerminalConfig.DIFF:
            raise ValueError(f"LabJack T8 only supports differential mode, but {channel.terminal_config} was provided.")

        return self._ai_channel_configs(channel)

    def _ai_channel_configs(
        self,
        channel: AnalogChannel | AnalogVoltageChannel,
    ) -> tuple[list[str], list[float] | list[int]]:
        range = self._compute_range(channel.range_min, channel.range_max)

        aNames = [f"{channel.physical_channel}_RANGE"]
        aValues = [range]

        return aNames, aValues

    def _compute_range(self, range_min: float, range_max: float) -> float:
        abs_range_max = max(abs(range_min), abs(range_max))

        valid_ranges = (r for r in self.VALID_RANGES if r >= abs_range_max)
        if valid_ranges is None:
            raise ValueError(
                f"No valid range found in {self.VALID_RANGES} for requested range_min={range_min}, range_max={range_max}"
            )

        return min(valid_ranges)

    def thermocouple_channel_configs(
        self,
        channel: AnalogThermocoupleChannel,
    ) -> tuple[list[str], list[float] | list[int]]:
        """T8 thermocouple AI config: ±0.075 V range on the isolated differential inputs."""
        if not (channel.physical_channel.startswith(self.AI_CHANNEL_PREFIX) and channel.physical_channel[3:].isdigit()):
            raise ValueError(
                f"Channel '{channel.physical_channel}' must be in the format '{self.AI_CHANNEL_PREFIX}#' where # is an integer"
            )

        return [f"{channel.physical_channel}_RANGE"], [self.TC_RANGE]

    def tc_cjc_read_name(self, physical_channel: str) -> str:
        """TEMPERATURE# is the streamable screw-terminal sensor (Kelvin) next to each AIN#."""
        return f"TEMPERATURE{physical_channel[3:]}"

    def refresh_tc_cjc(self, handle: int | None) -> None:
        """No-op; CJC is streamable."""

    def tc_cjc_kelvin(self, physical_channel: str, cjc_samples: dict[str, list[float]]) -> float:
        # CJC moves slowly; the batch's most recent sample is current enough.
        return cjc_samples[self.tc_cjc_read_name(physical_channel)][-1]

    def hw_timing_configs(
        self,
        hw_timing_config: HWTimingConfig,
        channels: list[AnalogChannelUnion],
        stream_buffer_bytes: int = 0,
    ) -> tuple[list[str], list[float] | list[int]]:
        aNames = [
            "STREAM_TRIGGER_INDEX",
            "STREAM_CLOCK_SOURCE",
            "STREAM_RESOLUTION_INDEX",
            "STREAM_BUFFER_SIZE_BYTES",
        ]
        aValues = [0, 0, 0, stream_buffer_bytes]

        return aNames, aValues
