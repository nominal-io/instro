import time
from ctypes import POINTER, addressof, c_double, c_ulong, c_ulonglong, c_ushort, cast, memmove, sizeof
from dataclasses import dataclass
from typing import Mapping

from mcculw import ul
from mcculw.device_info import DaqDeviceInfo
from mcculw.enums import (
    AiChanType,
    AnalogInputMode,
    BoardInfo,
    ChannelType,
    DigitalIODirection,
    DigitalPortType,
    FunctionType,
    InfoType,
    InterfaceType,
    ScanOptions,
    Status,
    ULRange,
)
from mcculw.ul import ULError

from instro.daq import DAQDriverBase
from instro.daq.drivers import HWTimestamper
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
    TerminalConfig,
)
from instro.lib import Measurement


@dataclass
class MCCDAQData:
    data: list[float]
    timestamp: int
    dt: int | None


class MCCDriver(DAQDriverBase):
    """MCC (Universal Library / mcculw) DAQ driver."""

    def __init__(self, device_id: str, buffer_multiplier: int = 2):
        """Initialize the MCC DAQ driver.

        Args:
            device_id: Device unique ID, optionally with a board number as
                ``"<serial>:<board_number>"`` (e.g. ``"344371:0"``). Defaults to board 0.
            buffer_multiplier: Circular-buffer size relative to per-fetch size.
                Higher values tolerate more jitter at the cost of memory.
        """
        super().__init__()
        self._info: DaqDeviceInfo | None = None
        if ":" in device_id:
            serial, board_number = device_id.split(":", 1)
            self._device_id = serial
            self._board_number = int(board_number)
        else:
            self._device_id = device_id
            self._board_number = 0
        self._buffer_multiplier = buffer_multiplier

        self._memhandle: int = 0
        self._buffer_size: int = 0  # Actual buffer size (fetch_size * multiplier)

        self._samples_consumed: int = 0  # Track consumed samples for streaming reads
        self._raw_count_prev: int = 0  # Last raw (unsigned-32) cur_count, for rollover reconstruction
        self._count_offset: int = 0  # Accumulated 2**32 rollovers of cur_count
        self._actual_sample_period: int = 0
        self._timestamper: HWTimestamper | None = None

        self._ai_channel_ranges: dict[str, ULRange] = {}  # Cache for resolved ULRange per AI channel
        self._ao_channel_ranges: dict[str, ULRange] = {}  # Cache for resolved ULRange per AO channel

    def open(self):
        """Connect to MCC device."""
        try:
            ul.ignore_instacal()  # bypasses configuration from InstaCal software
            devices = ul.get_daq_device_inventory(InterfaceType.ANY)
            device = next((dev for dev in devices if dev.unique_id == self._device_id), None)
            if not device:
                available = [dev.unique_id for dev in devices]
                raise RuntimeError(
                    f"Failed to connect to MCC device: no device with unique_id '{self._device_id}' found. "
                    f"Available devices: {available or 'none detected'}. "
                    "Check that the device is plugged in and powered, and that the mcculw driver can see it "
                    "(e.g. via InstaCal)."
                )
            ul.create_daq_device(self._board_number, device)
            self._info = DaqDeviceInfo(self._board_number)
        except ULError as e:
            raise RuntimeError(
                f"Failed to connect to MCC device '{self._device_id}' on board {self._board_number}: {e}. "
                "Check that the device is connected and that the board number is not already in use by another process."
            ) from e

    def close(self):
        """Disconnect from MCC device."""
        # Ensure any active scan is stopped and the scan buffer is freed, even if stop()
        # was not called explicitly (e.g. an exception between start() and stop()).
        self.stop()
        try:
            ul.release_daq_device(self._board_number)
        except Exception:
            pass
        finally:
            self._info = None

    def get_info(self) -> DaqDeviceInfo:
        """Underlying mcculw ``DaqDeviceInfo``."""
        if self._info is None:
            raise RuntimeError("Device not connected")

        return self._info

    @staticmethod
    def _get_terminal_config(
        terminal_config: TerminalConfig | None,
    ) -> AnalogInputMode | None:
        match terminal_config:
            case None:
                return None
            case TerminalConfig.DIFF:
                return AnalogInputMode.DIFFERENTIAL
            case TerminalConfig.RSE:
                return AnalogInputMode.SINGLE_ENDED
            case TerminalConfig.NRSE:
                raise ValueError("MCC DAQ does not support non-referenced single-ended mode.")
            case _:
                raise ValueError(
                    f"Invalid terminal configuration: {terminal_config}, must be one of {[cfg.name for cfg in TerminalConfig]}"
                )

    @staticmethod
    def _get_analog_channel_type(terminal_config: TerminalConfig | None) -> ChannelType:
        match terminal_config:
            case None:
                return ChannelType.ANALOG
            case TerminalConfig.DIFF:
                return ChannelType.ANALOG_DIFF
            case TerminalConfig.RSE:
                return ChannelType.ANALOG_SE
            case TerminalConfig.NRSE:
                raise ValueError("MCC DAQ does not support non-referenced single-ended mode.")

    def _build_channel_lists(self) -> tuple[list[int | DigitalPortType], list[ChannelType], list[ULRange]]:
        """Return ``(channels, channel_types, gains)`` aligned by index for ``ul.daq_in_scan``."""
        channel_list: list[int | DigitalPortType] = []
        channel_type_list: list[ChannelType] = []
        gain_list: list[ULRange] = []

        # Add analog input channels
        for channel in self._ai_channels.values():
            channel_list.append(int(channel.physical_channel))
            channel_type_list.append(self._get_analog_channel_type(channel.terminal_config))
            gain_list.append(self._ai_channel_ranges[channel.physical_channel])

        # Uncomment when hardware timed digital input is supported
        # for channel in self._di_channels.values():
        #     if isinstance(channel, DigitalPortChannel):
        #         port = self._get_port(channel.physical_channel)
        #         channel_list.append(port.type)
        #         channel_type_list.append(self._get_digital_channel_type(port))
        #         gain_list.append(ULRange.NOTUSED)

        return channel_list, channel_type_list, gain_list

    def configure_ai_channel(self, channel: AnalogChannel):
        """Configure an analog input channel on the MCC DAQ device."""
        ai_info = self._info.get_ai_info()
        if not ai_info.is_supported:
            raise ValueError("Analog input is not supported by this device.")

        if not (channel.physical_channel.isdigit() and int(channel.physical_channel) < ai_info.num_chans):
            raise ValueError(
                f"Channel '{channel}' must be in the format '#' where # is an integer less than {ai_info.num_chans}"
            )

        if not channel.direction == Direction.INPUT:
            raise ValueError(f"Channel '{channel}' must be an input channel to configure an analog input channel")

        # set channel to voltage mode
        try:
            ul.set_config(
                InfoType.BOARDINFO,
                self._board_number,
                int(channel.physical_channel),
                BoardInfo.ADCHANTYPE,
                AiChanType.VOLTAGE,
            )
        except Exception:
            pass

        # set channel range
        ul_range = self._get_range(channel)
        self._ai_channel_ranges[channel.physical_channel] = ul_range
        try:
            ul.set_config(
                InfoType.BOARDINFO,
                self._board_number,
                int(channel.physical_channel),
                BoardInfo.RANGE,
                ul_range,
            )
        except Exception:
            pass

        # set channel terminal config of board
        # Software-timed does not support per-channel terminal config, each channel configuration will update board-wide terminal config
        try:
            ul.a_input_mode(self._board_number, self._get_terminal_config(channel.terminal_config))
        except Exception:
            pass

        self._ai_channels[channel.alias] = channel

    def configure_ao_channel(self, channel: AnalogChannel):
        """Configure an analog output channel on the MCC DAQ device."""
        ao_info = self._info.get_ao_info()
        if not ao_info.is_supported:
            raise ValueError("Analog output is not supported by this device.")

        if not (channel.physical_channel.isdigit() and int(channel.physical_channel) < ao_info.num_chans):
            raise ValueError(
                f"Channel '{channel}' must be in the format '#' where # is an integer less than {ao_info.num_chans}"
            )

        if not channel.direction == Direction.OUTPUT:
            raise ValueError(f"Channel '{channel}' must be an output channel to configure an analog output channel")

        # set channel range
        ul_range = self._get_range(channel)
        self._ao_channel_ranges[channel.physical_channel] = ul_range
        try:
            ul.set_config(
                InfoType.BOARDINFO,
                self._board_number,
                int(channel.physical_channel),
                BoardInfo.DACRANGE,
                ul_range,
            )
        except Exception:
            pass

        self._ao_channels[channel.alias] = channel

    def _get_range(self, channel: AnalogChannel) -> ULRange:
        # Find the tightest ULRange that includes the configured range
        valid_ranges = []

        for ul_range in ULRange:
            # Check if this ULRange can accommodate the channel's configured range
            if hasattr(ul_range, "range_min") and hasattr(ul_range, "range_max"):
                if ul_range.range_min <= channel.range_min and ul_range.range_max >= channel.range_max:
                    # Calculate the span of this range
                    span = ul_range.range_max - ul_range.range_min
                    valid_ranges.append((ul_range, span))

        if not valid_ranges:
            raise ValueError(
                f"No supported range found for channel {channel.physical_channel} "
                f"with range [{channel.range_min}, {channel.range_max}]. "
                "The configured range exceeds all available hardware ranges."
            )

        # Sort by span (ascending) to get the tightest range first
        valid_ranges.sort(key=lambda x: x[1])

        return valid_ranges[0][0]

    def configure_ai_hw_timing(self, hw_timing_config: HWTimingConfig):
        """Configure hardware timing for the specified channels."""
        ai_info = self._info.get_ai_info()
        if not ai_info.supports_scan:
            raise ValueError(
                "Analog input scanning is not supported by this device. "
                "Hardware-timed acquisition requires scan capability."
            )

        # TODO: mcculw supports per channel samples rates
        for channel in self._ai_channels.values():
            try:
                ul.set_config(
                    InfoType.BOARDINFO,
                    self._board_number,
                    int(channel.physical_channel),
                    BoardInfo.ADDATARATE,
                    int(hw_timing_config.sample_rate),
                )
            except Exception:
                pass

        self._ai_hw_timing_config = hw_timing_config

    def start(self, **kwargs):
        """Start the MCC DAQ device for hw timed data acquisition."""
        # Reset consumed counter and timestamper for new acquisition
        self._samples_consumed = 0
        self._raw_count_prev = 0
        self._count_offset = 0
        self._timestamper = None

        # Validate DAQ input scan capability before allocating resources
        daqi_info = self._info.get_daqi_info()
        if not daqi_info.is_supported:
            raise ValueError(
                "DAQ input scan (daq_in_scan) is not supported by this device. "
                "Consider using software-timed acquisition or a device that supports DaqInScan."
            )

        if self._ai_hw_timing_config is None:
            raise RuntimeError("configure_ai_sample_rate() must be called before starting the DAQ.")
        hw_timing_config = self._ai_hw_timing_config
        samples_per_channel = hw_timing_config.samples_per_channel
        ai_info = self._info.get_ai_info()

        channel_list, channel_type_list, gain_list = self._build_channel_lists()
        num_chans = len(channel_list)

        # Validate each channel type is supported for DAQ input scan on this device
        supported_channel_types = daqi_info.supported_channel_types
        for ch_type in channel_type_list:
            if ch_type not in supported_channel_types:
                raise ValueError(
                    f"Channel type '{ch_type.name}' is not supported for DAQ input scan on this device. "
                    f"Supported channel types: {[t.name for t in supported_channel_types]}"
                )

        fetch_size = num_chans * samples_per_channel

        # Allocate a larger buffer to prevent overruns during timing jitter
        # buffer_size = fetch_size * multiplier gives us (multiplier - 1) extra cycles of tolerance
        self._buffer_size = fetch_size * self._buffer_multiplier

        # allocate a buffer for the scan based on the supported scan options and device resolution
        scan_options = ScanOptions.BACKGROUND | ScanOptions.CONTINUOUS
        if ScanOptions.SCALEDATA in ai_info.supported_scan_options:
            scan_options |= ScanOptions.SCALEDATA
            self._memhandle = ul.scaled_win_buf_alloc(self._buffer_size)
            self._ctypes_array = cast(self._memhandle, POINTER(c_double))
        elif ai_info.resolution <= 16:
            self._memhandle = ul.win_buf_alloc(self._buffer_size)
            self._ctypes_array = cast(self._memhandle, POINTER(c_ushort))
        elif ai_info.resolution <= 32:
            self._memhandle = ul.win_buf_alloc_32(self._buffer_size)
            self._ctypes_array = cast(self._memhandle, POINTER(c_ulong))
        else:
            self._memhandle = ul.win_buf_alloc_64(self._buffer_size)
            self._ctypes_array = cast(self._memhandle, POINTER(c_ulonglong))

        if not self._memhandle:
            raise RuntimeError("Failed to allocate memory")

        # If daq_in_scan fails, free the buffer we just allocated — the scan never started,
        # so stop()/stop_background is not guaranteed to clean this up.
        try:
            actual_rate, actual_pretrig_count, actual_total_count = ul.daq_in_scan(
                self._board_number,
                channel_list,
                channel_type_list,
                gain_list,
                num_chans,
                int(hw_timing_config.sample_rate),
                0,
                self._buffer_size,
                self._memhandle,
                scan_options,
            )
        except Exception:
            try:
                ul.win_buf_free(self._memhandle)
            except Exception:
                pass
            self._memhandle = 0
            raise
        self._actual_sample_period = round(1e9 / actual_rate)

        requested_rate = hw_timing_config.sample_rate
        if abs(actual_rate - requested_rate) / requested_rate > 0.1:
            print(
                f"Warning: Requested sample rate ({requested_rate}) "
                f"differs from actual hardware sample rate ({actual_rate}) by more than 10%."
            )

    def get_actual_sample_rate(self) -> float | None:
        if self._actual_sample_period > 0:
            return 1e9 / self._actual_sample_period
        return None

    def stop(self, **kwargs):
        """Stop the MCC DAQ device."""
        self._timestamper = None
        try:
            ul.stop_background(self._board_number, FunctionType.DAQIFUNCTION)
        except Exception:
            pass
        finally:
            if self._memhandle:
                try:
                    ul.win_buf_free(self._memhandle)
                except Exception:
                    pass
                self._memhandle = 0

    def read_analog(self) -> MCCDAQData:
        """Read from analog input channels."""
        data = []
        ai_resolution = self._info.get_ai_info().resolution
        channel_list, channel_type_list, gain_list = self._build_channel_lists()
        if len(channel_list) == 0:
            raise ValueError("No analog input channels configured")
        for ch, ch_type, ch_gain in zip(channel_list, channel_type_list, gain_list):
            if ai_resolution <= 16:
                eng_value = ul.v_in(self._board_number, ch, ch_gain)
            else:
                eng_value = ul.v_in_32(self._board_number, ch, ch_gain)
            data.append(eng_value)
        timestamp = time.time_ns()

        return MCCDAQData(data=data, timestamp=timestamp, dt=None)

    def _accumulate_count(self, raw_count: int) -> int:
        """Reconstruct a monotonic 64-bit sample count from mcculw's signed-32-bit cur_count."""
        raw = raw_count & 0xFFFFFFFF
        if raw < self._raw_count_prev:
            self._count_offset += 1 << 32
        self._raw_count_prev = raw
        return raw + self._count_offset

    def fetch_analog(self) -> MCCDAQData:
        """Block until ``samples_per_channel`` new samples are available, then drain the circular buffer."""
        if not self._memhandle:
            raise RuntimeError("No active scan. Call start() before fetch_analog().")

        if self._ai_hw_timing_config is None:
            raise RuntimeError("configure_ai_sample_rate() must be called before fetching analog data.")
        samples_per_channel = self._ai_hw_timing_config.samples_per_channel
        ai_info = self._info.get_ai_info()
        ai_supported_scan_options = ai_info.supported_scan_options

        channel_list, channel_type_list, gain_list = self._build_channel_lists()
        num_chans = len(channel_list)
        fetch_size = num_chans * samples_per_channel

        # Determine the ctypes type based on buffer format (independent of the read loop)
        if ScanOptions.SCALEDATA in ai_supported_scan_options:
            ctype = c_double
            copy_func = ul.scaled_win_buf_to_array
        elif ai_info.resolution <= 16:
            ctype = c_ushort
            copy_func = ul.win_buf_to_array
        elif ai_info.resolution <= 32:
            ctype = c_ulong
            copy_func = ul.win_buf_to_array_32
        else:
            ctype = c_ulonglong
            copy_func = ul.win_buf_to_array_64

        loop_start = time.monotonic()

        # Outer loop retries if a near-overrun corrupts the copy (see torn-copy guard below).
        while True:
            if time.monotonic() - loop_start > 5:
                raise TimeoutError("fetch_analog timed out after 5s waiting for an uncorrupted sample window.")

            # Block until enough new samples are available. _samples_consumed tracks how many
            # samples we've already consumed from the stream.
            samples_needed = self._samples_consumed + fetch_size
            while True:
                status, raw_count, curr_index = ul.get_status(self._board_number, FunctionType.DAQIFUNCTION)

                # Wait for DAQ to be running
                if status == Status.IDLE or curr_index == -1:
                    if time.monotonic() - loop_start > 5:
                        raise TimeoutError("fetch_analog timed out after 5s waiting for DAQ to start producing data.")
                    time.sleep(0.01)
                    continue

                # mcculw cur_count is a signed-32-bit cumulative counter that rolls negative at
                # 2**31; reconstruct a monotonic 64-bit count before any comparison.
                curr_count = self._accumulate_count(raw_count)

                # Wait until enough NEW samples beyond what we've already consumed.
                self.points_in_buffer = curr_count - self._samples_consumed
                if curr_count < samples_needed:
                    if time.monotonic() - loop_start > 5:
                        raise TimeoutError(
                            f"fetch_analog timed out after 5s waiting for {fetch_size} samples "
                            f"(got {curr_count - (samples_needed - fetch_size)})."
                        )
                    time.sleep(0.01)
                    continue

                # We have enough new data
                timestamp = time.time_ns()
                break

            # Check for buffer overrun - the circular buffer contains samples [curr_count - _buffer_size, curr_count - 1]
            # If we wanted samples starting at _samples_consumed but they've been overwritten, we have data loss
            oldest_sample_in_buffer = curr_count - self._buffer_size
            if oldest_sample_in_buffer > self._samples_consumed:
                # cur_count advances in DMA packet increments, not multiples of num_chans, so the
                # oldest sample can land mid-scan. Round UP to the next full scan boundary so the
                # de-interleave / gain mapping stays channel-aligned and we never read overwritten data.
                remainder = oldest_sample_in_buffer % num_chans
                if remainder:
                    oldest_sample_in_buffer += num_chans - remainder
                samples_lost = oldest_sample_in_buffer - self._samples_consumed
                print(
                    f"Warning: Buffer overrun detected. {samples_lost} samples were overwritten before they could be read. "
                    f"Consider increasing buffer_multiplier or reducing the background loop interval."
                )
                # Skip ahead to the current buffer contents and re-establish the window.
                self._samples_consumed = oldest_sample_in_buffer
                continue

            # Calculate read position in circular buffer
            read_origin = self._samples_consumed
            read_start = read_origin % self._buffer_size

            # Allocate snapshot buffer
            buffer_snapshot = (ctype * fetch_size)()

            # Handle wrap-around: if read spans the end of the circular buffer, do two copies
            if read_start + fetch_size <= self._buffer_size:
                # No wrap-around - single contiguous copy
                copy_func(self._memhandle, buffer_snapshot, read_start, fetch_size)
            else:
                # Wrap-around - copy in two parts
                first_part_size = self._buffer_size - read_start
                second_part_size = fetch_size - first_part_size

                # Copy from read_start to end of buffer
                first_part = (ctype * first_part_size)()
                copy_func(self._memhandle, first_part, read_start, first_part_size)

                # Copy from beginning of buffer
                second_part = (ctype * second_part_size)()
                copy_func(self._memhandle, second_part, 0, second_part_size)

                # Combine into buffer_snapshot using memmove for performance
                memmove(buffer_snapshot, first_part, first_part_size * sizeof(ctype))
                memmove(
                    addressof(buffer_snapshot) + first_part_size * sizeof(ctype),
                    second_part,
                    second_part_size * sizeof(ctype),
                )

            # Torn-copy guard: DMA keeps writing during the copy above. If the oldest valid sample
            # has advanced past our read origin, part of this window was overwritten mid-copy.
            _, raw_after, _ = ul.get_status(self._board_number, FunctionType.DAQIFUNCTION)
            count_after = self._accumulate_count(raw_after)
            if count_after - self._buffer_size > read_origin:
                continue

            # Commit consumption only once we have an uncorrupted copy.
            self._samples_consumed = read_origin + fetch_size
            break

        # Process the snapshot to extract channel data
        if ScanOptions.SCALEDATA in ai_supported_scan_options:
            data = list(buffer_snapshot)
        elif ai_info.resolution <= 16:
            data = [
                ul.to_eng_units(self._board_number, gain_list[i % num_chans], buffer_snapshot[i])
                for i in range(fetch_size)
            ]
        else:
            data = [
                ul.to_eng_units_32(self._board_number, gain_list[i % num_chans], buffer_snapshot[i])
                for i in range(fetch_size)
            ]

        return MCCDAQData(data=data, timestamp=timestamp, dt=self._actual_sample_period)

    def write_analog_value(self, channel: AnalogChannel, value: float):
        """Write an analog value to an analog output channel."""
        if channel not in self._ao_channels.values():
            raise ValueError(f"Channel '{channel}' is not configured as an analog output channel")

        # TODO: add support for non-voltage output channels
        ul.v_out(
            self._board_number, int(channel.physical_channel), self._ao_channel_ranges[channel.physical_channel], value
        )

    def configure_di_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``DigitalPortType/#``, configure the bit for DI, and register the line."""
        channel = self._build_line_channel(physical_channel, Direction.INPUT, logic, logic_level, alias)
        port = self._get_port(channel.physical_channel)
        try:
            ul.d_config_bit(self._board_number, port.type, channel.bit_position, DigitalIODirection.IN)
        except Exception as e:
            raise RuntimeError(
                f"Device does not support per-bit digital configuration for port {port.type.name}. "
                f"Configure the entire port as a DigitalPortChannel instead, then use read_digital_line "
                f"to read specific lines on the port."
            ) from e
        self._di_channels[channel.alias] = channel

    def configure_do_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``DigitalPortType/#``, configure the bit for DO, and register the line."""
        channel = self._build_line_channel(physical_channel, Direction.OUTPUT, logic, logic_level, alias)
        port = self._get_port(channel.physical_channel)
        try:
            ul.d_config_bit(self._board_number, port.type, channel.bit_position, DigitalIODirection.OUT)
        except Exception as e:
            raise RuntimeError(
                f"Device does not support per-bit digital configuration for port {port.type.name}. "
                f"Configure the entire port as a DigitalPortChannel instead, then use write_digital_line "
                f"to write to specific lines on the port."
            ) from e
        self._do_channels[channel.alias] = channel

    def configure_di_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``DigitalPortType``, configure the port for DI, and register the port."""
        channel = self._build_port_channel(physical_channel, Direction.INPUT, logic, port_width, logic_level, alias)
        port = self._get_port(channel.physical_channel)
        try:
            ul.d_config_port(self._board_number, port.type, DigitalIODirection.IN)
        except Exception:
            pass
        self._di_channels[channel.alias] = channel

    def configure_do_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``DigitalPortType``, configure the port for DO, and register the port."""
        channel = self._build_port_channel(physical_channel, Direction.OUTPUT, logic, port_width, logic_level, alias)
        port = self._get_port(channel.physical_channel)
        try:
            ul.d_config_port(self._board_number, port.type, DigitalIODirection.OUT)
        except Exception:
            pass
        self._do_channels[channel.alias] = channel

    def _build_line_channel(
        self,
        physical_channel: str,
        direction: Direction,
        logic: Logic,
        logic_level: float | None,
        alias: str | None,
    ) -> DigitalLineChannel:
        if not self._info.get_dio_info().is_supported:
            raise ValueError("Digital I/O is not supported by this device, cannot define digital channels.")
        if "/" not in physical_channel:
            raise ValueError(
                "physical_channel does not define the line within the channel to create a channel from. "
                "Define the physical channel as DigitalPortType/#, where # is the decimal bit position of the line within the port, ex. 'FIRSTPORTA/0' or 'AUXPORT0/1'."
            )
        # Validate the port exists on this device.
        self._get_port(physical_channel)
        _, bit = physical_channel.split("/")
        return DigitalLineChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=direction,
            logic_level=logic_level,
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
        if not self._info.get_dio_info().is_supported:
            raise ValueError("Digital I/O is not supported by this device, cannot define digital channels.")
        if "/" in physical_channel:
            raise ValueError(
                f"port_width is set to {port_width} but physical_channel implies a line. "
                "Define the physical channel as the enum field of the DigitalPortType, ex. 'AUXPORT0' or 'FIRSTPORTA'."
                f" Received {physical_channel}."
            )
        port = self._get_port(physical_channel)
        if port_width != port.num_bits:
            raise ValueError(
                f"MCC DAQ does not support user-configurable port widths. port_width must match the number of bits in the defined port. "
                f"Received {port_width} for port {port}, but the number of bits in the port is {port.num_bits}."
            )
        return DigitalPortChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=direction,
            logic_level=logic_level,
            logic=logic,
            width=port_width,
        )

    def _get_port(self, physical_channel: str) -> DigitalPortType:
        """Get the port type from the physical channel."""
        dio_port_info = self._info.get_dio_info().port_info
        for port in dio_port_info:
            if port.type == DigitalPortType[physical_channel.split("/")[0]]:
                return port
        raise ValueError(
            f"Port {physical_channel.split('/')[0]} is not supported by device {self._device_id}. Supported ports are: {[port.type for port in dio_port_info]}"
        )

    @staticmethod
    def _get_digital_channel_type(port: DigitalPortType) -> ChannelType:
        if port.num_bits == 8:
            return ChannelType.DIGITAL8
        elif port.num_bits == 16:
            return ChannelType.DIGITAL16
        else:
            return ChannelType.DIGITAL

    def write_digital_line(self, channel: DigitalChannel, data: int):
        """Write 0/1 to a single DO line (``DigitalLineChannel``)."""
        if not isinstance(channel, DigitalLineChannel):
            raise TypeError(
                f"write_digital_line expects a DigitalLineChannel, got {type(channel).__name__}. "
                "Use write_digital_port for port-wide writes."
            )
        port = self._get_port(channel.physical_channel)
        if data not in (0, 1):
            raise ValueError(
                f"Writing a value of {data} to a digital line channel is not supported. Only 0 and 1 are supported."
            )
        if channel.logic is Logic.LOW:
            data = 1 - data
        ul.d_bit_out(self._board_number, port.type, channel.bit_position, data)

    def read_digital_line(self, channel: DigitalChannel) -> int:
        """Read 0/1 from a single DI line (``DigitalLineChannel``)."""
        if not isinstance(channel, DigitalLineChannel):
            raise TypeError(
                f"read_digital_line expects a DigitalLineChannel, got {type(channel).__name__}. "
                "Use read_digital_port for port-wide reads."
            )
        port = self._get_port(channel.physical_channel)
        data = ul.d_bit_in(self._board_number, port.type, channel.bit_position)
        if channel.logic is Logic.LOW:
            data = 1 - data
        return data

    def write_digital_port(self, channel: DigitalChannel, data: int):
        """Write an N-bit value to a DO port (bit *i* drives line *i*)."""
        if not isinstance(channel, DigitalPortChannel):
            raise TypeError(
                f"write_digital_port expects a DigitalPortChannel, got {type(channel).__name__}. "
                "Use write_digital_line for single-bit writes."
            )
        port = self._get_port(channel.physical_channel)
        if channel.logic is Logic.LOW:
            mask = (1 << int(channel.width)) - 1
            data = data ^ mask
        ul.d_out(self._board_number, port.type, data)

    def read_digital_port(self, channel: DigitalChannel) -> int:
        """Read an N-bit value from a DI port (bit *i* reflects line *i*)."""
        if not isinstance(channel, DigitalPortChannel):
            raise TypeError(
                f"read_digital_port expects a DigitalPortChannel, got {type(channel).__name__}. "
                "Use read_digital_line for single-bit reads."
            )
        port = self._get_port(channel.physical_channel)
        data = ul.d_in(self._board_number, port.type)
        if channel.logic is Logic.LOW:
            mask = (1 << int(channel.width)) - 1
            data = data ^ mask
        return data

    def _read_to_measurements(
        self,
        response: MCCDAQData,
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs,
    ) -> list[Measurement]:
        num_channels = len(channel_list)
        samples_per_channel = len(response.data) // num_channels

        # De-interleave the data
        channel_data = {}
        for i, channel in enumerate(channel_list):
            channel_data[f"{daq_name}.{channel}"] = response.data[i::num_channels]

        if response.dt:
            if self._timestamper is None:
                self._timestamper, timestamps = HWTimestamper.seed(response.timestamp, response.dt, samples_per_channel)
            else:
                timestamps = self._timestamper.next_batch(response.dt, samples_per_channel)
        else:
            timestamps = [response.timestamp]

        return [
            Measurement(
                channel_data=channel_data,
                timestamps=timestamps,
                tags={**default_tags, **(kwargs or {})},
            )
        ]
