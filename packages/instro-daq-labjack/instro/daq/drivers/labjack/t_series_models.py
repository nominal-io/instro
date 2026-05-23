from typing import Protocol

from instro.daq.types import AnalogChannel, DAQChannel, HWTimingConfig, TerminalConfig


class LJ_Model(Protocol):
    MIN_SCAN_RATE: float
    MAX_SCAN_RATE: float

    def ai_channel_configs(
        self,
        channel: AnalogChannel,
    ) -> tuple[list[str], list[float] | list[int]]: ...

    def hw_timing_configs(
        self,
        hw_timing_config: HWTimingConfig,
        channels: list[AnalogChannel],
    ) -> tuple[list[str], list[float] | list[int]]: ...


class LJ_T4:
    """LabJack T4 device model constants."""

    AI_CHANNEL_PREFIX = "AIN"
    AO_CHANNEL_PREFIX = "DAC"
    VALID_RANGES = [10]
    MIN_SCAN_RATE = 0.0157
    MAX_SCAN_RATE = 50000.0

    def ai_channel_configs(
        self,
        channel: AnalogChannel,
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

    def _ai_channel_configs(self, channel: AnalogChannel) -> tuple[list[str], list[float] | list[int]]:
        """T4 has no per-channel AI config; returns empty names/values."""
        aNames = []  # type: ignore
        aValues = []  # type: ignore

        return aNames, aValues

    def hw_timing_configs(
        self,
        hw_timing_config: HWTimingConfig,
        channels: list[AnalogChannel],
    ) -> tuple[list[str], list[float] | list[int]]:
        # Stream settling is 0 (default) and
        # stream resolution index is 0 (default).

        aNames = ["STREAM_SETTLING_US", "STREAM_RESOLUTION_INDEX"]
        aValues = [0, 0]

        return aNames, aValues


class LJ_T7:
    """LabJack T7 device model constants."""

    AI_CHANNEL_PREFIX = "AIN"
    AO_CHANNEL_PREFIX = "DAC"
    VALID_RANGES = [10.0, 1.0, 0.1, 0.01]
    MIN_SCAN_RATE = 0.0157
    MAX_SCAN_RATE = 100000.0

    def ai_channel_configs(
        self,
        channel: AnalogChannel,
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
        channel: AnalogChannel,
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
        channels: list[AnalogChannel],
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
        ]
        aValues = [0, 0, 0]

        return aNames, aValues


class LJ_T8:
    """LabJack T8 device model constants."""

    AI_CHANNEL_PREFIX = "AIN"
    AO_CHANNEL_PREFIX = "DAC"
    VALID_RANGES = [11.0, 9.6, 4.8, 2.4, 1.2, 0.6, 0.3, 0.15, 0.075, 0.036, 0.015]
    MIN_SCAN_RATE = 20.0
    MAX_SCAN_RATE = 40000.0

    def ai_channel_configs(self, channel: AnalogChannel) -> tuple[list[str], list[float] | list[int]]:
        if not (channel.physical_channel.startswith(self.AI_CHANNEL_PREFIX) and channel.physical_channel[3:].isdigit()):
            raise ValueError(
                f"Channel '{channel.physical_channel}' must be in the format '{self.AI_CHANNEL_PREFIX}#' where # is an integer"
            )

        if channel.terminal_config and channel.terminal_config != TerminalConfig.DIFF:
            raise ValueError(f"LabJack T8 only supports differential mode, but {channel.terminal_config} was provided.")

        return self._ai_channel_configs(channel)

    def _ai_channel_configs(
        self,
        channel: AnalogChannel,
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

    def hw_timing_configs(
        self,
        hw_timing_config: HWTimingConfig,
        channels: list[AnalogChannel],
    ) -> tuple[list[str], list[float] | list[int]]:
        aNames = [
            "STREAM_TRIGGER_INDEX",
            "STREAM_CLOCK_SOURCE",
            "STREAM_RESOLUTION_INDEX",
        ]
        aValues = [0, 0, 0]

        return aNames, aValues
