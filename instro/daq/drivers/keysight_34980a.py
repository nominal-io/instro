"""Keysight 34980A Multifunction Switch/Measure Unit DAQ driver."""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Mapping, cast

from instro.daq import DAQDriverBase
from instro.daq.types import (
    AnalogChannel,
    DAQChannel,
    DigitalChannel,
    DigitalLineChannel,
    DigitalPortChannel,
    DigitalPortWidth,
    Direction,
    HWTimingConfig,
    Logic,
    RelayChannel,
)
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.lib.types import Measurement

# A single grouped 34950A digital channel addresses at most 32 bits (LWORd). WIDTH_64 spans
# separate banks and must be configured as two channels, so it is rejected at configure time.
_PORT_WIDTH_TOKENS = {
    DigitalPortWidth.WIDTH_8: "BYTE",
    DigitalPortWidth.WIDTH_16: "WORD",
    DigitalPortWidth.WIDTH_32: "LWOR",
}


@dataclass
class KeysightData:
    data: str
    timestamp: int | None = None
    dt: int | None = None


def keysight_str_to_ns(ts_str: str) -> int:
    """Convert a Keysight ``"YYYY,MM,DD,HH,MM,SS.sss"`` timestamp (UTC) to ns since the Unix epoch."""
    parts = ts_str.split(",")
    if len(parts) != 6:
        raise ValueError(f"Unexpected timestamp format: {ts_str}")
    year, month, day, hour, minute = map(int, parts[:5])
    second = float(parts[5])
    whole_seconds = int(second)
    microseconds = int((second - whole_seconds) * 1e6)
    dt = datetime(
        year,
        month,
        day,
        hour,
        minute,
        whole_seconds,
        microseconds,
        tzinfo=timezone.utc,
    )
    return int(dt.timestamp() * 1e9)


def parse_datastring(data: str) -> tuple[list[float], list[int]]:
    """Split a Keysight reading/timestamp data string into ``(measurements, timestamps_ns)``."""
    tokens = data.split(",")

    chunk_size = 7
    chunks = [list(islice(tokens, i, i + chunk_size)) for i in range(0, len(tokens), chunk_size)]

    readings = list(map(lambda chunk: float(chunk[0]), chunks))
    timestamps = list(map(lambda chunk: keysight_str_to_ns(",".join(chunk[1:])), chunks))

    return readings, timestamps


def get_scanlist(channels: list[DAQChannel]) -> list[DAQChannel]:
    # From Keysight 34980A Multifunction Switch/ Measure Unit Programmer's Reference
    # By default, the instrument scans the list of channels in ascending order from slot 1 through slot 8 (channels
    # are reordered as needed). If your application requires non-ordered scanning of the channels in the present
    # scan list, you can use the ROUTe:SCAN:ORDered command to enable the non-sequential scanning mode. In
    # either mode, channels which are not in the scan list are skipped during the scan.
    # a. For sequential scanning (default, ROUT:SCAN:ORDERED ON), the specified channels are reordered as
    # needed and duplicate channels are eliminated. For example, (@2001,1003,1001,1003) will be interpreted
    # as (@1001,1003,2001).
    #
    # b. For non-sequential scanning (ROUT:SCAN:ORDERED OFF), the channels remain in the order presented
    # in the scan list (see exception below). Multiple occurrences of the same channel are allowed. For
    # example, (@2001,2001,2001) and (@3010,1003,1001,1005) are valid and the channels will be scanned in
    # the order presented.

    # c. When you specify a range of channels in the scan list, the channels are always sorted in ascending order,
    # regardless of the ROUTe:SCAN:ORDered setting. Therefore, (@1009:1001) will always be interpreted as
    # 1001, 1002, 1003, etc.
    new_list = channels.copy()
    new_list.sort(key=lambda ch: int(ch.physical_channel))
    return new_list


class Keysight34980A(DAQDriverBase):
    """Keysight 34980A Multifunction Switch/Measure Unit."""

    def __init__(
        self,
        visa_resource: str | VisaConfig,
        *,
        sync_system_clock: bool = True,
    ) -> None:
        """Initialize the driver.

        Args:
            visa_resource: VISA resource string or full ``VisaConfig``.
            sync_system_clock: Sync the instrument clock to host UTC on ``open()``
                so returned timestamps align with the host. Enabled by default.
        """
        super().__init__()
        self._visa = VisaDriver(visa_resource)
        self._sync_system_clock = sync_system_clock

    def open(self):
        self._visa.open()
        with self._visa.lock():
            self._visa.write("*RST")
            self._visa.write("*CLS")
            self._check_errors()

        if self._sync_system_clock:
            self._sync_to_system_datetime()

    def close(self):
        self._visa.close()

    def configure_ai_channel(
        self,
        channel: AnalogChannel,
    ):
        """Configure an AI channel: ``CONF:VOLT:DC`` at computed range, then add to ``ROUT:SCAN`` and enable timestamps."""
        range = self._compute_ai_range(channel)

        with self._visa.lock():
            self._visa.write(f"CONF:VOLT:DC {range}, 0.003, (@{channel.physical_channel})")
            self._visa.write(f"ROUTe:SCAN:ADD (@{channel.physical_channel})")
            self._turn_on_timestamps()
            self._check_errors()

        self._ai_channels[channel.alias] = channel

    def configure_ai_hw_timing(
        self,
        hw_timing_config: HWTimingConfig,
    ):
        """Configure ``TRIG:SOUR TIMER`` at the configured sample period and infinite count."""
        with self._visa.lock():
            self._visa.write("TRIG:SOUR TIMER")
            self._visa.write(f"TRIG:TIM {hw_timing_config.sample_period / 1e9}")
            self._visa.write("TRIG:COUN INF")
            self._check_errors()

        self._ai_hw_timing_config = hw_timing_config

    def start(self, **kwargs):
        """Enable timestamps and ``INIT`` the scan."""
        with self._visa.lock():
            self._turn_on_timestamps()
            self._visa.write("INIT")
            self._check_errors()

    def stop(self, **kwargs):
        """``ABORt`` any pending scan."""
        with self._visa.lock():
            self._visa.write("ABORt")
            self._check_errors()

    def read_analog(self) -> KeysightData:
        scan_string = ",".join([ch.physical_channel for ch in self._ai_channels.values()])

        with self._visa.lock():
            response = self._visa.query(f"READ? (@{scan_string})")
            self._check_errors()

        return KeysightData(data=response)

    def fetch_analog(
        self,
    ) -> KeysightData:
        """Block until the buffer holds at least one full per-channel batch, then drain a channel-aligned chunk."""
        if self._ai_hw_timing_config is None:
            raise RuntimeError("configure_ai_sample_rate() must be called before fetching analog data.")
        num_channels = len(self._ai_channels)
        min_points_per_fetch = self._ai_hw_timing_config.samples_per_channel * num_channels

        with self._visa.lock():
            # Create a blocking call
            # TODO create a timeout
            while True:
                points = int(self._visa.query("DATA:POIN?"))
                self.points_in_buffer = points

                if points >= min_points_per_fetch:
                    # Grab as many points as possible but not more than modulus the number of channels
                    response = self._visa.query(f"DATA:REM? {(points // num_channels) * num_channels}")
                    self._check_errors()
                    return KeysightData(data=response)

                time.sleep(0.001)

    # ====== DIGITAL ========

    def configure_di_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``MNNN/B`` (slot/channel/bit), program the port for DI, and register the line."""
        channel = self._build_line_channel(physical_channel, Direction.INPUT, logic, logic_level, alias)
        self._program_port(channel, direction=Direction.INPUT)
        self._di_channels[channel.alias] = channel

    def configure_do_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``MNNN/B`` (slot/channel/bit), program the port for DO, and register the line."""
        channel = self._build_line_channel(physical_channel, Direction.OUTPUT, logic, logic_level, alias)
        self._program_port(channel, direction=Direction.OUTPUT)
        self._do_channels[channel.alias] = channel

    def configure_di_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``MNNN`` (slot/channel), program the port for DI, and register the port."""
        channel = self._build_port_channel(physical_channel, Direction.INPUT, logic, port_width, logic_level, alias)
        self._program_port(channel, direction=Direction.INPUT)
        self._di_channels[channel.alias] = channel

    def configure_do_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``MNNN`` (slot/channel), program the port for DO, and register the port."""
        channel = self._build_port_channel(physical_channel, Direction.OUTPUT, logic, port_width, logic_level, alias)
        self._program_port(channel, direction=Direction.OUTPUT)
        self._do_channels[channel.alias] = channel

    def _build_line_channel(
        self,
        physical_channel: str,
        direction: Direction,
        logic: Logic,
        logic_level: float | None,
        alias: str | None,
    ) -> DigitalLineChannel:
        if "/" not in physical_channel:
            raise ValueError(
                "physical_channel does not define the bit within the channel to create a channel from. "
                "Define the physical channel as MNNN/B where M is the slot, NNN is the channel, and B is bit. ex '5101/3'."
            )
        channel_name, bit = physical_channel.split("/")
        return DigitalLineChannel(
            physical_channel=channel_name,
            alias=alias or physical_channel,
            direction=direction,
            logic_level=logic_level or 3.3,
            logic=logic,
            bit_position=int(bit),
        )

    def _build_port_channel(
        self,
        physical_channel: str,
        direction: Direction,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None,
        alias: str | None,
    ) -> DigitalPortChannel:
        if "/" in physical_channel:
            raise ValueError(
                f"port_width is set to {port_width} but physical_channel implies a line. "
                "Define the physical channel as MNNN where M is the slot and NNN is the channel. ex '5101'."
                f" Received {physical_channel}."
            )
        if port_width not in _PORT_WIDTH_TOKENS:
            raise ValueError(
                f"Keysight 34980A digital ports support up to 32-bit ({DigitalPortWidth.WIDTH_32!r}); "
                f"got {port_width!r}. Configure a 64-bit span as two separate channels."
            )
        return DigitalPortChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=direction,
            logic_level=logic_level or 3.3,
            logic=logic,
            width=port_width,
        )

    def _program_port(self, channel: DigitalChannel, direction: Direction) -> None:
        dir_token = "INP" if direction is Direction.INPUT else "OUTP"
        width_token = _PORT_WIDTH_TOKENS[channel.width] if isinstance(channel, DigitalPortChannel) else "BYTE"
        with self._visa.lock():
            self._visa.write(f"CONF:DIG:WIDT {width_token},(@{channel.physical_channel})")
            self._visa.write(f"CONF:DIG:DIR {dir_token},(@{channel.physical_channel})")
            self._visa.write(
                f"CONF:DIG:POL {'INV' if channel.logic is Logic.LOW else 'NORM'},(@{channel.physical_channel})"
            )
            self._visa.write(f"SOUR:DIG:DRIV ACT,(@{channel.physical_channel})")
            self._visa.write(f"SOUR:DIG:LEV {channel.logic_level:.2f},(@{channel.physical_channel})")
            self._check_errors()

    def write_digital_line(
        self,
        channel: DigitalChannel,
        data: int,
    ) -> None:
        # Cast to DigitalLineChannel since we know Keysight uses this type
        line_channel = cast(DigitalLineChannel, channel)
        with self._visa.lock():
            self._visa.write(
                f"SOUR:DIG:DATA:BIT {str(data)},{line_channel.bit_position}, (@{line_channel.physical_channel})"
            )
            self._check_errors()

    def read_digital_line(self, channel: DigitalChannel) -> int:
        # Cast to DigitalLineChannel since we know Keysight uses this type
        line_channel = cast(DigitalLineChannel, channel)
        with self._visa.lock():
            response = self._visa.query(
                f"SENS:DIG:DATA:BIT? {line_channel.bit_position}, (@{line_channel.physical_channel})"
            )
            self._check_errors()

        return int(response)

    def write_digital_port(self, channel: DigitalChannel, data: int):
        """Drive a DO port as one N-bit integer. Active-low is applied in hardware via CONF:DIG:POL."""
        if not isinstance(channel, DigitalPortChannel):
            raise TypeError(
                f"write_digital_port expects a DigitalPortChannel, got {type(channel).__name__}. "
                "Use write_digital_line for single-bit writes."
            )
        width_token = _PORT_WIDTH_TOKENS[channel.width]
        with self._visa.lock():
            self._visa.write(f"SOUR:DIG:DATA:{width_token} {data},(@{channel.physical_channel})")
            self._check_errors()

    def read_digital_port(self, channel: DigitalChannel) -> int:
        """Sample a DI port as one N-bit integer. Active-low is applied in hardware via CONF:DIG:POL."""
        if not isinstance(channel, DigitalPortChannel):
            raise TypeError(
                f"read_digital_port expects a DigitalPortChannel, got {type(channel).__name__}. "
                "Use read_digital_line for single-bit reads."
            )
        width_token = _PORT_WIDTH_TOKENS[channel.width]
        with self._visa.lock():
            response = self._visa.query(f"SENS:DIG:DATA:{width_token}? (@{channel.physical_channel})")
            self._check_errors()

        return int(response)

    # ====== RELAY ========

    def close_relay(self, channel: RelayChannel):
        """``ROUTe:CLOSe`` the relay."""
        with self._visa.lock():
            self._visa.write(f"ROUTe:CLOSe (@{channel.physical_channel})")
            self._check_errors()

    def open_relay(self, channel: RelayChannel):
        """``ROUTe:OPEN`` the relay."""
        with self._visa.lock():
            self._visa.write(f"ROUTe:OPEN (@{channel.physical_channel})")
            self._check_errors()

    def _check_errors(self) -> None:
        err = self._visa.query("SYST:ERR?")
        parts = err.strip().split(",", 1)
        code_str = parts[0] if parts else ""
        code_val = int(code_str) if code_str.lstrip("-+").isdigit() else -1
        if code_val != 0:
            raise RuntimeError(f"Keysight 34980A reported error: {err.strip()}")

    def _turn_on_timestamps(self):
        self._visa.write("FORM:READ:TIME ON")
        self._visa.write("FORM:READ:TIME:TYPE ABS")

    def _sync_to_system_datetime(
        self,
    ):
        # Get current UTC time from system and set units to match
        now = datetime.now(timezone.utc)

        with self._visa.lock():
            self._visa.write(f"SYST:DATE {now.year},{now.month},{now.day}")
            self._visa.write(f"SYST:TIME {now.hour},{now.minute},{now.second + now.microsecond * 1e-6:.3f}")
            self._check_errors()

    def _compute_ai_range(self, channel: AnalogChannel) -> float:
        ranges = [0.1, 1.0, 10.0, 100.0, 300.0]
        highest_abs = max(abs(channel.range_min), abs(channel.range_max))

        for value in ranges:
            if value >= highest_abs:
                return value

        return ranges[-1]

    def _read_to_measurements(
        self,
        response: KeysightData,
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs,
    ) -> list[Measurement]:
        num_channels = len(channel_list)
        readings, timestamps = parse_datastring(response.data)
        scan_list = get_scanlist(list(channel_list.values()))

        measurements: list[Measurement] = []
        for i, ch in enumerate(scan_list):
            channel_data = {}
            channel_data[f"{daq_name}.{ch.alias}"] = readings[i::num_channels]
            measurement = Measurement(
                channel_data=channel_data,
                timestamps=timestamps[i::num_channels],
                tags={**default_tags, **(kwargs or {})},
            )
            measurements.append(measurement)

        return measurements
