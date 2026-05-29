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

        self.ai_channels[channel.alias] = channel

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

        self.ai_hw_timing_config = hw_timing_config

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
        scan_string = ",".join([ch.physical_channel for ch in self.ai_channels.values()])

        with self._visa.lock():
            response = self._visa.query(f"READ? (@{scan_string})")
            self._check_errors()

        return KeysightData(data=response)

    def fetch_analog(
        self,
    ) -> KeysightData:
        """Block until the buffer holds at least one full per-channel batch, then drain a channel-aligned chunk."""
        if self.ai_hw_timing_config is None:
            raise RuntimeError("configure_ai_sample_rate() must be called before fetching analog data.")
        num_channels = len(self.ai_channels)
        min_points_per_fetch = self.ai_hw_timing_config.samples_per_channel * num_channels

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

    def define_digital_channel(
        self,
        direction: Direction,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
        port_width: DigitalPortWidth | None = None,
    ) -> DigitalChannel:
        # A port should be defined as MNNN where M is slot and NNN is channel. ex '5101'
        # A line should be defined as MNNN/B where M is slot, NNN is channel, and B is bit. ex '5101/3'
        alias = alias or physical_channel

        # Logic level is configurable on these devices. Set to 3.3V if nothing is provided.
        logic_level = logic_level or 3.3

        if port_width:
            if "/" in physical_channel:
                raise ValueError(
                    f"port_width is set to {port_width} but physical_channel implies a line. "
                    "Define the physical channel as MNNN where M is the slot and NNN is the channel. ex '5101'."
                    f"Received {physical_channel}."
                )

            return DigitalPortChannel(
                physical_channel=physical_channel,
                alias=alias,
                direction=direction,
                logic_level=logic_level,
                logic=logic,
                width=port_width,
            )

        if "/" not in physical_channel:
            raise ValueError(
                "physical_channel does not define the bit within the channel to create a channel from. "
                "Define the physical channel as MNNN/B where M is the slot, NNN is the channel, and B is bit. ex '5101/3'."
            )

        channel_name, bit = physical_channel.split("/")

        channel = DigitalLineChannel(
            physical_channel=channel_name,
            alias=alias,
            direction=direction,
            logic_level=logic_level,  # type: ignore
            logic=logic,
            bit_position=int(bit),
        )
        return channel

    def configure_do_channel(
        self,
        channel: DigitalChannel,
    ):
        """Configure DO width/direction/polarity/drive/level for ``channel``."""
        with self._visa.lock():
            self._visa.write(f"CONF:DIG:WIDT BYTE,(@{channel.physical_channel})")
            self._visa.write(f"CONF:DIG:DIR OUTP,(@{channel.physical_channel})")
            self._visa.write(
                f"CONF:DIG:POL {'INV' if channel.logic is Logic.LOW else 'NORM'},(@{channel.physical_channel})"
            )
            self._visa.write(f"SOUR:DIG:DRIV ACT,(@{channel.physical_channel})")
            self._visa.write(f"SOUR:DIG:LEV {channel.logic_level:.2f},(@{channel.physical_channel})")
            self._check_errors()

        self.do_channels[channel.alias] = channel

    def configure_di_channel(
        self,
        channel: DigitalChannel,
    ):
        """Configure DI width/direction/polarity/drive/level for ``channel``."""
        with self._visa.lock():
            self._visa.write(f"CONF:DIG:WIDT BYTE,(@{channel.physical_channel})")
            self._visa.write(f"CONF:DIG:DIR INP,(@{channel.physical_channel})")
            self._visa.write(
                f"CONF:DIG:POL {'INV' if channel.logic is Logic.LOW else 'NORM'},(@{channel.physical_channel})"
            )
            self._visa.write(f"SOUR:DIG:DRIV ACT,(@{channel.physical_channel})")
            self._visa.write(f"SOUR:DIG:LEV {channel.logic_level:.2f},(@{channel.physical_channel})")
            self._check_errors()

        self.di_channels[channel.alias] = channel

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
        raise NotImplementedError("write_digital_port is not yet implemented for Keysight 34980A.")

    def read_digital_port(self, channel: DigitalChannel) -> int:
        raise NotImplementedError("read_digital_port is not yet implemented for Keysight 34980A.")

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
