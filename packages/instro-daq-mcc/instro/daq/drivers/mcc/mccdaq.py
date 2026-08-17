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
    ErrorCode,
    FunctionType,
    InfoType,
    InterfaceType,
    ScanOptions,
    Status,
    TcType,
    TempScale,
    ULRange,
)
from mcculw.ul import ULError

from instro.daq import DAQDriverBase
from instro.daq.drivers import HWTimestamper
from instro.daq.scaling.thermocouple import TC_UNIT
from instro.daq.types import (
    AnalogChannel,
    AnalogChannelUnion,
    AnalogCurrentChannel,
    AnalogThermocoupleChannel,
    AnalogVoltageChannel,
    CJCSource,
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

# The range AiChanType.CURRENT reads through. The UL exposes no way to query it: on a current-mode
# channel get_config(BoardInfo.RANGE) reports -1, ADNUMSERANGES/ADNUMDIFFRANGES report 0, and
# ai_info.supported_ranges only probes voltage ranges. Its bounds are read back with _get_range_bounds.
CURRENT_RANGE = ULRange.BIPPT025AMPS


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
        self._scan_scaled: bool = False  # whether the running scan fills the buffer with engineering units

        self._ai_channel_ranges: dict[str, ULRange] = {}  # Cache for resolved ULRange per AI channel
        self._ao_channel_ranges: dict[str, ULRange] = {}  # Cache for resolved ULRange per AO channel
        self._ai_supported_ranges: list[ULRange] = []  # Device AI ranges, captured at open()

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
            # Capture the AI ranges before any channel is programmed. The UL derives this list from
            # BoardInfo.RANGE, so once a channel's range is set it collapses to just that range.
            ai_info = self._info.get_ai_info()
            self._ai_supported_ranges = list(ai_info.supported_ranges) if ai_info.is_supported else []
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
    def _get_temp_scale(unit: TC_UNIT) -> TempScale:
        match unit:
            case TC_UNIT.CELSIUS:
                return TempScale.CELSIUS
            case TC_UNIT.FAHRENHEIT:
                return TempScale.FAHRENHEIT
            case TC_UNIT.KELVIN:
                return TempScale.KELVIN
            case _:
                raise ValueError(
                    f"MCC DAQ does not support the {unit.name} temperature scale, "
                    f"must be one of {[u.name for u in (TC_UNIT.CELSIUS, TC_UNIT.FAHRENHEIT, TC_UNIT.KELVIN)]}"
                )

    def _ordered_ai_channels(self) -> list[AnalogChannelUnion]:
        """AI channels in ascending physical-channel order, the order the channel/gain queue requires."""
        return sorted(self._ai_channels.values(), key=lambda channel: int(channel.physical_channel))

    def _get_tc_range(self) -> ULRange:
        """Range the thermocouple front end uses; it is always the device's most sensitive range."""
        return min(self._ai_supported_ranges, key=lambda ul_range: ul_range.range_max - ul_range.range_min)

    def _get_range_bounds(self, ul_range: ULRange) -> tuple[float, float]:
        """Engineering units this board resolves a range's lowest and highest counts to."""
        resolution = self._info.get_ai_info().resolution
        full_scale = (1 << resolution) - 1
        to_eng_units = ul.to_eng_units if resolution <= 16 else ul.to_eng_units_32
        return (
            to_eng_units(self._board_number, ul_range, 0),
            to_eng_units(self._board_number, ul_range, full_scale),
        )

    def _get_current_range(self, channel: AnalogCurrentChannel) -> ULRange:
        """Check the channel's requested range against what the current front end reads, then return it."""
        low, high = self._get_range_bounds(CURRENT_RANGE)
        if channel.range_min < low or channel.range_max > high:
            raise ValueError(
                f"Channel '{channel.physical_channel}' requested range [{channel.range_min}, {channel.range_max}] A, "
                f"but this device's current input covers [{low}, {high}] A."
            )
        return CURRENT_RANGE

    def _program_ai_channel(self, channel: AnalogChannelUnion, chan_type: AiChanType, ul_range: ULRange):
        """Program a channel's input type and range, then cache the range for scans."""
        try:
            ul.set_config(
                InfoType.BOARDINFO,
                self._board_number,
                int(channel.physical_channel),
                BoardInfo.ADCHANTYPE,
                chan_type,
            )
        except ULError as e:
            # single-purpose MCC devices don't expose this config item and raise when calling this configuration
            # continue if set_config raises on this issue
            if e.errorcode not in (ErrorCode.BADCONFIGITEM, ErrorCode.BADBOARDTYPE):
                raise

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

        self._ai_channel_ranges[channel.physical_channel] = ul_range

    def _validate_ai_channel(self, channel: AnalogChannelUnion, num_chans: int):
        """Reject channels the device cannot serve as an analog input."""
        if not (channel.physical_channel.isdigit() and int(channel.physical_channel) < num_chans):
            raise ValueError(
                f"Channel '{channel}' must be in the format '#' where # is an integer less than {num_chans}"
            )

        if not channel.direction == Direction.INPUT:
            raise ValueError(f"Channel '{channel}' must be an input channel to configure an analog input channel")

    def configure_ai_channel(self, channel: AnalogChannel):
        """Deprecated: use ``configure_ai_voltage_channel``. Configure an analog input channel on the MCC DAQ device."""
        self.configure_ai_voltage_channel(channel)

    def configure_ai_voltage_channel(self, channel: AnalogChannel | AnalogVoltageChannel):
        """Configure a voltage analog input channel on the MCC DAQ device."""
        ai_info = self._info.get_ai_info()
        if not ai_info.is_supported:
            raise ValueError("Analog input is not supported by this device.")

        self._validate_ai_channel(channel, ai_info.num_chans)

        self._program_ai_channel(channel, AiChanType.VOLTAGE, self._get_range(channel, self._ai_supported_ranges))

        # set channel terminal config of board
        # Software-timed does not support per-channel terminal config, each channel configuration will update board-wide terminal config
        try:
            ul.a_input_mode(self._board_number, self._get_terminal_config(channel.terminal_config))
        except Exception:
            pass

        self._ai_channels[channel.alias] = channel

    def configure_ai_current_channel(self, channel: AnalogCurrentChannel):
        """Configure a current input channel on the MCC DAQ device."""
        ai_info = self._info.get_ai_info()
        if not ai_info.is_supported:
            raise ValueError("Analog input is not supported by this device.")

        self._validate_ai_channel(channel, ai_info.num_chans)

        self._program_ai_channel(channel, AiChanType.CURRENT, self._get_current_range(channel))

        self._ai_channels[channel.alias] = channel

    def configure_ao_current_channel(self, channel: AnalogCurrentChannel):
        """Current output is not supported by this driver."""
        raise NotImplementedError("Current output has not been implemented for the MCC driver.")

    def configure_ai_thermocouple_channel(self, channel: AnalogThermocoupleChannel):
        """Configure a thermocouple input channel on the MCC DAQ device."""
        ai_info = self._info.get_ai_info()
        if not ai_info.temp_supported:
            raise ValueError("Temperature input is not supported by this device.")

        self._validate_ai_channel(channel, ai_info.num_temp_chans)

        if channel.cjc_source not in (None, CJCSource.INTERNAL):
            raise ValueError("MCC DAQ applies cold-junction compensation internally; cjc_source must be INTERNAL.")

        if channel.tc_input_scaler is not None:
            raise ValueError(
                "tc_input_scaler is not supported by the MCC driver; the device returns temperature directly."
            )

        temp_scale = self._get_temp_scale(channel.unit)

        # TEMPSCALE is board-wide, so scanned temperatures come back in one unit for every thermocouple channel.
        conflicting = [
            other.alias
            for other in self._ai_channels.values()
            if isinstance(other, AnalogThermocoupleChannel) and other.unit is not channel.unit
        ]
        if conflicting:
            raise ValueError(
                f"MCC DAQ applies one board-wide temperature scale, but channels {conflicting} are already "
                f"configured in a different unit than '{channel.alias}' ({channel.unit.name})."
            )

        self._program_ai_channel(channel, AiChanType.TC, self._get_tc_range())

        # set thermocouple type on channel
        ul.set_config(
            InfoType.BOARDINFO,
            self._board_number,
            int(channel.physical_channel),
            BoardInfo.CHANTCTYPE,
            TcType[channel.tc_type.value],
        )

        # (hw timed) sets the unit a_in_scan returns for thermocouple channels;
        ul.set_config(InfoType.BOARDINFO, self._board_number, 0, BoardInfo.TEMPSCALE, temp_scale)

        self._ai_channels[channel.alias] = channel

    def configure_ao_channel(self, channel: AnalogChannel):
        """Deprecated: use ``configure_ao_voltage_channel``. Configure an analog output channel on the MCC DAQ device."""
        self.configure_ao_voltage_channel(channel)

    def configure_ao_voltage_channel(self, channel: AnalogChannel | AnalogVoltageChannel):
        """Configure a voltage analog output channel on the MCC DAQ device."""
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
        ul_range = self._get_range(channel, ao_info.supported_ranges)
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

    def _get_range(self, channel: AnalogChannel | AnalogVoltageChannel, supported_ranges: list[ULRange]) -> ULRange:
        """Return the tightest range the device offers that spans the channel's configured range."""
        # Only ranges the device actually offers are candidates; picking any other ULRange is accepted
        # by set_config and then fails the read or write with a range error.
        valid_ranges = [
            ul_range
            for ul_range in supported_ranges
            if hasattr(ul_range, "range_min")
            and hasattr(ul_range, "range_max")
            and ul_range.range_min <= channel.range_min
            and ul_range.range_max >= channel.range_max
        ]

        if not valid_ranges:
            raise ValueError(
                f"No supported range found for channel {channel.physical_channel} "
                f"with range [{channel.range_min}, {channel.range_max}]. "
                f"This device supports {[ul_range.name for ul_range in supported_ranges] or 'no ranges'}."
            )

        return min(valid_ranges, key=lambda ul_range: ul_range.range_max - ul_range.range_min)

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

        if self._ai_hw_timing_config is None:
            raise RuntimeError("configure_ai_sample_rate() must be called before starting the DAQ.")
        if not self._ai_channels:
            raise ValueError("No analog input channels configured")
        hw_timing_config = self._ai_hw_timing_config
        samples_per_channel = hw_timing_config.samples_per_channel
        ai_info = self._info.get_ai_info()

        if not ai_info.supports_scan:
            raise ValueError(
                "Analog input scanning is not supported by this device. "
                "Hardware-timed acquisition requires scan capability."
            )

        ordered_channels = self._ordered_ai_channels()
        channels = [int(channel.physical_channel) for channel in ordered_channels]
        gains = [self._ai_channel_ranges[channel.physical_channel] for channel in ordered_channels]
        num_chans = len(channels)

        # a_in_scan samples one contiguous range at a single range unless a channel/gain queue overrides
        # it, which is also what gives each channel type its own range within a scan.
        if ai_info.supports_gain_queue:
            ul.a_load_queue(self._board_number, channels, gains, num_chans)
        elif channels != list(range(channels[0], channels[0] + num_chans)):
            raise ValueError(
                f"This device has no channel/gain queue, so a scan covers a contiguous channel range, but "
                f"the configured channels are {channels}. Configure a contiguous range with no gaps."
            )
        elif len(set(gains)) > 1:
            raise ValueError(
                f"This device has no channel/gain queue, so one range applies to the whole scan, but the "
                f"configured channels resolved to {[ul_range.name for ul_range in dict.fromkeys(gains)]}. "
                "Give every channel in the scan the same range."
            )

        fetch_size = num_chans * samples_per_channel

        # Allocate a larger buffer to prevent overruns during timing jitter
        # buffer_size = fetch_size * multiplier gives us (multiplier - 1) extra cycles of tolerance
        self._buffer_size = fetch_size * self._buffer_multiplier

        # allocate a buffer for the scan based on the supported scan options and device resolution
        scan_options = ScanOptions.BACKGROUND | ScanOptions.CONTINUOUS
        self._scan_scaled = ScanOptions.SCALEDATA in ai_info.supported_scan_options

        # Raw counts convert through a voltage range, which cannot linearize a thermocouple.
        if not self._scan_scaled:
            thermocouples = [
                channel.alias for channel in ordered_channels if isinstance(channel, AnalogThermocoupleChannel)
            ]
            if thermocouples:
                raise ValueError(
                    f"Hardware-timed acquisition of thermocouple channels {thermocouples} requires the SCALEDATA "
                    "scan option, which is not supported by this device. Use software-timed reads (read_analog)."
                )

        if self._scan_scaled:
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

        # If the scan fails, free the buffer we just allocated — the scan never started,
        # so stop()/stop_background is not guaranteed to clean this up.
        try:
            actual_rate = ul.a_in_scan(
                self._board_number,
                min(channels),
                max(channels),
                self._buffer_size,
                int(hw_timing_config.sample_rate),
                gains[0],
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
            ul.stop_background(self._board_number, FunctionType.AIFUNCTION)
            # clear the channel/gain queue so it does not carry into later scans or software-timed reads
            ul.a_load_queue(self._board_number, [], [], 0)
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
        if not self._ai_channels:
            raise ValueError("No analog input channels configured")
        for channel in self._ordered_ai_channels():
            ch = int(channel.physical_channel)
            ul_range = self._ai_channel_ranges[channel.physical_channel]
            if isinstance(channel, AnalogThermocoupleChannel):
                # (sw timed) read thermocouple channels in their configured unit.
                eng_value = ul.t_in(self._board_number, ch, self._get_temp_scale(channel.unit))
            elif isinstance(channel, AnalogCurrentChannel):
                # v_in rejects a current-mode channel, so scale the raw count against the current range
                if ai_resolution <= 16:
                    eng_value = ul.to_eng_units(self._board_number, ul_range, ul.a_in(self._board_number, ch, ul_range))
                else:
                    eng_value = ul.to_eng_units_32(
                        self._board_number, ul_range, ul.a_in_32(self._board_number, ch, ul_range)
                    )
            elif ai_resolution <= 16:
                eng_value = ul.v_in(self._board_number, ch, ul_range)
            else:
                eng_value = ul.v_in_32(self._board_number, ch, ul_range)
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

        num_chans = len(self._ai_channels)
        fetch_size = num_chans * samples_per_channel

        # Determine the ctypes type from the buffer format start() allocated
        if self._scan_scaled:
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
                status, raw_count, curr_index = ul.get_status(self._board_number, FunctionType.AIFUNCTION)

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
            _, raw_after, _ = ul.get_status(self._board_number, FunctionType.AIFUNCTION)
            count_after = self._accumulate_count(raw_after)
            if count_after - self._buffer_size > read_origin:
                continue

            # Commit consumption only once we have an uncorrupted copy.
            self._samples_consumed = read_origin + fetch_size
            break

        # Process the snapshot to extract channel data
        if self._scan_scaled:
            data = list(buffer_snapshot)
            return MCCDAQData(data=data, timestamp=timestamp, dt=self._actual_sample_period)

        # Raw counts only come back when the device cannot scale in hardware, which rules out linearized types.
        gain_list = [self._ai_channel_ranges[channel.physical_channel] for channel in self._ordered_ai_channels()]
        if ai_info.resolution <= 16:
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
        """Deprecated: use ``configure_di_channel``. Parse ``DigitalPortType/#`` and register the DI line."""
        self.configure_di_channel(
            DigitalLineChannel(
                physical_channel=physical_channel,
                alias=alias or physical_channel,
                direction=Direction.INPUT,
                logic_level=logic_level,
                logic=logic,
            )
        )

    def configure_di_channel(self, channel: DigitalLineChannel):
        """Parse ``DigitalPortType/#``, configure the bit for DI, and register the line."""
        channel = self._build_line_channel(
            channel.physical_channel, Direction.INPUT, channel.logic, channel.logic_level, channel.alias
        )
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
        """Deprecated: use ``configure_do_channel``. Parse ``DigitalPortType/#`` and register the DO line."""
        self.configure_do_channel(
            DigitalLineChannel(
                physical_channel=physical_channel,
                alias=alias or physical_channel,
                direction=Direction.OUTPUT,
                logic_level=logic_level,
                logic=logic,
            )
        )

    def configure_do_channel(self, channel: DigitalLineChannel):
        """Parse ``DigitalPortType/#``, configure the bit for DO, and register the line."""
        channel = self._build_line_channel(
            channel.physical_channel, Direction.OUTPUT, channel.logic, channel.logic_level, channel.alias
        )
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

        # De-interleave the data. Both read paths walk channels in ascending physical-channel order,
        # so samples align with that order rather than the order the channels were configured in.
        aliases = [channel.alias for channel in sorted(channel_list.values(), key=lambda c: int(c.physical_channel))]
        channel_data = {}
        for i, alias in enumerate(aliases):
            channel_data[f"{daq_name}.{alias}"] = response.data[i::num_channels]

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
