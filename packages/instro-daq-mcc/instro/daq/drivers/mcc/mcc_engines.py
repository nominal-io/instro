"""Scan engines: one class per MCC hardware-timed acquisition architecture."""

from ctypes import Array, c_double, c_ulong, c_ulonglong, c_ushort
from typing import Callable, Protocol

from mcculw import ul
from mcculw.enums import ChannelType, DigitalPortType, FunctionType, ScanOptions, TempScale, ULRange

from instro.daq.scaling.thermocouple import TC_UNIT
from instro.daq.types import AnalogChannelUnion, AnalogThermocoupleChannel, HWTimingConfig, TerminalConfig


def get_temp_scale(unit: TC_UNIT) -> TempScale:
    """Map a TC_UNIT to the mcculw TempScale of the same name."""
    try:
        return TempScale[unit.name]
    except KeyError:
        raise ValueError(f"MCC DAQ does not support the {unit.name} temperature scale.") from None


class ScanEngine(Protocol):
    """One acquisition architecture; the driver drains the circular buffer, the engine owns the rest."""

    function_type: FunctionType
    memhandle: int
    buffer_size: int
    scan_width: int
    ctype: type
    copy_func: Callable
    aliases: list[str]
    actual_rate: float

    def start(
        self,
        channels: list[AnalogChannelUnion],
        channel_ranges: dict[str, ULRange],
        hw_timing_config: HWTimingConfig,
        buffer_multiplier: int,
    ) -> None: ...

    def convert(self, buffer_snapshot: Array, samples_per_channel: int) -> list[float]: ...

    def stop(self) -> None: ...


class DaqInScanEngine:
    """Composite scanner (``daq_in_scan``): typed channel/gain lists, raw counts, TC via CJC pairing."""

    function_type = FunctionType.DAQIFUNCTION

    def __init__(self, board_num: int, ai_info, daqi_info):
        self._board_num = board_num
        self._ai_info = ai_info
        self._daqi_info = daqi_info
        self._scaled = False
        self._channel_list: list[int | DigitalPortType] = []
        self._channel_type_list: list[ChannelType] = []
        self._gain_list: list[ULRange] = []
        self._tc_unit: TC_UNIT | None = None
        self.memhandle = 0
        self.buffer_size = 0
        self.scan_width = 0
        self.ctype: type = c_ushort
        self.copy_func: Callable = ul.win_buf_to_array
        self.aliases: list[str] = []
        self.actual_rate = 0.0

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

    @staticmethod
    def _get_cjc_channel(tc_channel: int) -> int:
        """CJC sensor serving a TC channel, per the UL pairing table (CJC0->TC0; CJC1->TC1,TC2; CJC2->TC3; repeats)."""
        return 3 * (tc_channel // 4) + (0, 1, 1, 2)[tc_channel % 4]

    def _build_channel_lists(
        self, channels: list[AnalogChannelUnion], channel_ranges: dict[str, ULRange]
    ) -> tuple[list[int | DigitalPortType], list[ChannelType], list[ULRange], list[str]]:
        """Return ``(channels, channel_types, gains, aliases)`` for ``ul.daq_in_scan``.

        The first three lists align by scan position and include CJC entries; ``aliases`` holds the
        user channels only, in scan order, because CJC samples never leave the driver.
        """
        channel_list: list[int | DigitalPortType] = []
        channel_type_list: list[ChannelType] = []
        gain_list: list[ULRange] = []
        alias_list: list[str] = []

        # Analog channels go first: daq_in_scan wants ANALOG entries ahead of other channel types.
        for channel in channels:
            if isinstance(channel, AnalogThermocoupleChannel):
                continue
            channel_list.append(int(channel.physical_channel))
            channel_type_list.append(self._get_analog_channel_type(channel.terminal_config))
            gain_list.append(channel_ranges[channel.physical_channel])
            alias_list.append(channel.alias)

        # Each TC entry must immediately follow its associated CJC entry; TCs sharing a CJC share one entry.
        tc_channels = sorted(
            (channel for channel in channels if isinstance(channel, AnalogThermocoupleChannel)),
            key=lambda channel: int(channel.physical_channel),
        )
        last_cjc = None
        for channel in tc_channels:
            cjc_channel = self._get_cjc_channel(int(channel.physical_channel))
            if cjc_channel != last_cjc:
                channel_list.append(cjc_channel)
                channel_type_list.append(ChannelType.CJC)
                gain_list.append(ULRange.NOTUSED)
                last_cjc = cjc_channel
            channel_list.append(int(channel.physical_channel))
            channel_type_list.append(ChannelType.TC)
            gain_list.append(ULRange.NOTUSED)
            alias_list.append(channel.alias)

        return channel_list, channel_type_list, gain_list, alias_list

    def start(
        self,
        channels: list[AnalogChannelUnion],
        channel_ranges: dict[str, ULRange],
        hw_timing_config: HWTimingConfig,
        buffer_multiplier: int,
    ) -> None:
        """Validate the channel set, allocate the scan buffer, and launch the background scan."""
        channel_list, channel_type_list, gain_list, aliases = self._build_channel_lists(channels, channel_ranges)
        num_chans = len(channel_list)

        # Validate each channel type is supported for DAQ input scan on this device
        supported_channel_types = self._daqi_info.supported_channel_types
        for ch_type in channel_type_list:
            if ch_type not in supported_channel_types:
                raise ValueError(
                    f"Channel type '{ch_type.name}' is not supported for DAQ input scan on this device. "
                    f"Supported channel types: {[t.name for t in supported_channel_types]}"
                )

        fetch_size = num_chans * hw_timing_config.samples_per_channel

        # Allocate a larger buffer to prevent overruns during timing jitter
        # buffer_size = fetch_size * multiplier gives us (multiplier - 1) extra cycles of tolerance
        self.buffer_size = fetch_size * buffer_multiplier

        # allocate a buffer for the scan based on the supported scan options and device resolution
        scan_options = ScanOptions.BACKGROUND | ScanOptions.CONTINUOUS
        self._scaled = ScanOptions.SCALEDATA in self._ai_info.supported_scan_options
        if self._scaled:
            scan_options |= ScanOptions.SCALEDATA
            self.memhandle = ul.scaled_win_buf_alloc(self.buffer_size)
            self.ctype, self.copy_func = c_double, ul.scaled_win_buf_to_array
        elif self._ai_info.resolution <= 16:
            self.memhandle = ul.win_buf_alloc(self.buffer_size)
            self.ctype, self.copy_func = c_ushort, ul.win_buf_to_array
        elif self._ai_info.resolution <= 32:
            self.memhandle = ul.win_buf_alloc_32(self.buffer_size)
            self.ctype, self.copy_func = c_ulong, ul.win_buf_to_array_32
        else:
            self.memhandle = ul.win_buf_alloc_64(self.buffer_size)
            self.ctype, self.copy_func = c_ulonglong, ul.win_buf_to_array_64

        if not self.memhandle:
            raise RuntimeError("Failed to allocate memory")

        # If daq_in_scan fails, free the buffer we just allocated — the scan never started,
        # so stop()/stop_background is not guaranteed to clean this up.
        try:
            actual_rate, actual_pretrig_count, actual_total_count = ul.daq_in_scan(
                self._board_num,
                channel_list,
                channel_type_list,
                gain_list,
                num_chans,
                int(hw_timing_config.sample_rate),
                0,
                self.buffer_size,
                self.memhandle,
                scan_options,
            )
        except Exception:
            try:
                ul.win_buf_free(self.memhandle)
            except Exception:
                pass
            self.memhandle = 0
            raise

        self._channel_list = channel_list
        self._channel_type_list = channel_type_list
        self._gain_list = gain_list
        self._tc_unit = next(
            (channel.unit for channel in channels if isinstance(channel, AnalogThermocoupleChannel)), None
        )
        self.scan_width = num_chans
        self.aliases = aliases
        self.actual_rate = actual_rate

    def convert(self, buffer_snapshot: Array, samples_per_channel: int) -> list[float]:
        """Snapshot -> user-channel engineering units in scan order, with CJC columns dropped."""
        if self._scaled:
            return list(buffer_snapshot)

        to_eng_units = ul.to_eng_units if self._ai_info.resolution <= 16 else ul.to_eng_units_32

        temps: list[float] = []
        if ChannelType.TC in self._channel_type_list:
            temps = self._convert_tc_counts(buffer_snapshot, samples_per_channel)

        data: list[float] = []
        temp_index = 0
        for i in range(len(buffer_snapshot)):
            chan_type = self._channel_type_list[i % self.scan_width]
            if chan_type == ChannelType.CJC:
                continue
            if chan_type == ChannelType.TC:
                data.append(temps[temp_index])
                temp_index += 1
            else:
                data.append(to_eng_units(self._board_num, self._gain_list[i % self.scan_width], buffer_snapshot[i]))
        return data

    def _convert_tc_counts(self, buffer_snapshot: Array, samples_per_channel: int) -> list[float]:
        """Convert a snapshot's raw TC samples to temperatures; get_tc_values only reads UL buffers."""
        count = len(buffer_snapshot)
        if self.ctype is c_ushort:
            scratch = ul.win_buf_alloc(count)
            copy_to_buf = ul.win_array_to_buf
        elif self.ctype is c_ulong:
            scratch = ul.win_buf_alloc_32(count)
            copy_to_buf = ul.win_array_to_buf_32
        else:
            raise NotImplementedError("Thermocouple conversion is not supported for >32-bit scan buffers.")
        if not scratch:
            raise RuntimeError("Failed to allocate memory")
        try:
            copy_to_buf(buffer_snapshot, scratch, 0, count)
            _, temps = ul.get_tc_values(
                self._board_num,
                self._channel_list,
                self._channel_type_list,
                len(self._channel_list),
                scratch,
                0,
                samples_per_channel,
                get_temp_scale(self._tc_unit),
            )
            return list(temps)
        finally:
            ul.win_buf_free(scratch)

    def stop(self) -> None:
        """Stop the background scan and free the scan buffer."""
        try:
            ul.stop_background(self._board_num, self.function_type)
        except Exception:
            pass
        finally:
            if self.memhandle:
                try:
                    ul.win_buf_free(self.memhandle)
                except Exception:
                    pass
                self.memhandle = 0


class ScaledAInScanEngine:
    """Simple scanner (``a_in_scan`` + SCALEDATA): gain queue carries per-channel ranges, device scales samples."""

    function_type = FunctionType.AIFUNCTION

    def __init__(self, board_num: int, ai_info):
        self._board_num = board_num
        self._ai_info = ai_info
        self.memhandle = 0
        self.buffer_size = 0
        self.scan_width = 0
        self.ctype: type = c_double
        self.copy_func: Callable = ul.scaled_win_buf_to_array
        self.aliases: list[str] = []
        self.actual_rate = 0.0

    def start(
        self,
        channels: list[AnalogChannelUnion],
        channel_ranges: dict[str, ULRange],
        hw_timing_config: HWTimingConfig,
        buffer_multiplier: int,
    ) -> None:
        """Load the channel/gain queue, allocate the scaled buffer, and launch the background scan."""
        # a_in_scan reads a contiguous ascending channel span, so scan in ascending physical-channel
        # order and let the channel/gain queue narrow the span and give each channel its own range.
        ordered = sorted(channels, key=lambda channel: int(channel.physical_channel))
        channel_numbers = [int(channel.physical_channel) for channel in ordered]
        gains = [channel_ranges[channel.physical_channel] for channel in ordered]
        num_chans = len(ordered)

        # Every SCALEDATA-capable AI device in the UL catalog also has a channel/gain queue; requiring
        # it gives every channel its own range instead of validating single-range corner cases.
        if not self._ai_info.supports_gain_queue:
            raise ValueError(
                "This device has no channel/gain queue, so hardware-timed acquisition is not supported "
                "by this driver. Use software-timed reads (read_analog) instead."
            )
        ul.a_load_queue(self._board_num, channel_numbers, gains, num_chans)

        fetch_size = num_chans * hw_timing_config.samples_per_channel

        # Allocate a larger buffer to prevent overruns during timing jitter
        # buffer_size = fetch_size * multiplier gives us (multiplier - 1) extra cycles of tolerance
        self.buffer_size = fetch_size * buffer_multiplier

        self.memhandle = ul.scaled_win_buf_alloc(self.buffer_size)
        if not self.memhandle:
            raise RuntimeError("Failed to allocate memory")

        # If a_in_scan fails, free the buffer we just allocated — the scan never started,
        # so stop()/stop_background is not guaranteed to clean this up.
        try:
            actual_rate = ul.a_in_scan(
                self._board_num,
                channel_numbers[0],
                channel_numbers[-1],
                self.buffer_size,
                int(hw_timing_config.sample_rate),
                gains[0],  # ignored: the loaded channel/gain queue defines each channel's range
                self.memhandle,
                ScanOptions.BACKGROUND | ScanOptions.CONTINUOUS | ScanOptions.SCALEDATA,
            )
        except Exception:
            try:
                ul.win_buf_free(self.memhandle)
            except Exception:
                pass
            self.memhandle = 0
            raise

        self.scan_width = num_chans
        self.aliases = [channel.alias for channel in ordered]
        self.actual_rate = actual_rate

    def convert(self, buffer_snapshot: Array, samples_per_channel: int) -> list[float]:
        """SCALEDATA already produced engineering units per each channel's configured input type."""
        return list(buffer_snapshot)

    def stop(self) -> None:
        """Stop the background scan, clear the channel/gain queue, and free the scan buffer."""
        try:
            ul.stop_background(self._board_num, self.function_type)
            # clear the channel/gain queue so it does not leak into later scans or software reads
            ul.a_load_queue(self._board_num, [], [], 0)
        except Exception:
            pass
        finally:
            if self.memhandle:
                try:
                    ul.win_buf_free(self.memhandle)
                except Exception:
                    pass
                self.memhandle = 0
