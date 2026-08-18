"""Logic specific to the different kinds of scanning engines an MCC DAQ can have."""

import logging
import math
from ctypes import Array, c_double, c_ulong, c_ulonglong, c_ushort
from dataclasses import dataclass
from typing import Callable, Protocol

from mcculw import ul
from mcculw.device_info import DaqDeviceInfo
from mcculw.enums import ChannelType, DigitalPortType, ErrorCode, FunctionType, ScanOptions, TempScale, ULRange

from instro.daq.scaling.thermocouple import TC_UNIT
from instro.daq.types import (
    AnalogChannelUnion,
    AnalogCurrentChannel,
    AnalogThermocoupleChannel,
    HWTimingConfig,
    TerminalConfig,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCCPortInfo:
    type: DigitalPortType
    num_bits: int


@dataclass(frozen=True)
class MCCDeviceInfo:
    """Capabilities captured once at open.

    """Capabilities captured once at open; omits AoInfo.supported_ranges (its probe drives AO0)."""
    """

    product_name: str
    unique_id: str
    ai_supported: bool
    ai_temp_supported: bool
    ai_num_temp_chans: int
    ai_resolution: int
    ai_supported_ranges: tuple[ULRange, ...]
    ai_supports_scan: bool
    ai_supported_scan_options: ScanOptions
    ai_supports_gain_queue: bool
    ao_supported: bool
    ao_num_chans: int
    daqi_supported: bool
    dio_supported: bool
    dio_ports: tuple[MCCPortInfo, ...]

    @classmethod
    def snapshot(cls, board_number: int) -> "MCCDeviceInfo":
        """Read every capability once: some probes actively drive the hardware (a_in test calls)."""
        info = DaqDeviceInfo(board_number)
        ai = info.get_ai_info()
        ao = info.get_ao_info()
        daqi = info.get_daqi_info()
        dio = info.get_dio_info()
        supports_scan = ai.supports_scan
        return cls(
            product_name=info.product_name,
            unique_id=info.unique_id,
            ai_supported=ai.is_supported,
            ai_temp_supported=ai.temp_supported,
            ai_num_temp_chans=ai.num_temp_chans,
            ai_resolution=ai.resolution,
            ai_supported_ranges=tuple(ai.supported_ranges) if ai.is_supported else (),
            ai_supports_scan=supports_scan,
            ai_supported_scan_options=ai.supported_scan_options if supports_scan else ScanOptions(0),
            ai_supports_gain_queue=ai.supports_gain_queue,
            ao_supported=ao.is_supported,
            ao_num_chans=ao.num_chans,
            daqi_supported=daqi.is_supported,
            dio_supported=dio.is_supported,
            dio_ports=tuple(MCCPortInfo(type=port.type, num_bits=port.num_bits) for port in dio.port_info),
        )


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

    def __init__(self, board_num: int, device_info: MCCDeviceInfo):
        self._board_num = board_num
        self._device_info = device_info
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

    def _build_channel_lists(
        self, channels: list[AnalogChannelUnion], channel_ranges: dict[str, ULRange]
    ) -> tuple[list[int | DigitalPortType], list[ChannelType], list[ULRange], list[str]]:
        """Return ``(channels, channel_types, gains, aliases)`` for ``ul.daq_in_scan``; aliases exclude CJC entries."""
        channel_list: list[int | DigitalPortType] = []
        channel_type_list: list[ChannelType] = []
        gain_list: list[ULRange] = []
        alias_list: list[str] = []

        # Analog channels go first: daq_in_scan wants ANALOG entries ahead of other channel types.
        for channel in channels:
            if isinstance(channel, AnalogThermocoupleChannel):
                continue
            if isinstance(channel, AnalogCurrentChannel):
                # daq_in_scan boards don't support a current front end
                raise ValueError(
                    f"Channel '{channel.alias}': daq_in_scan devices do not support hardware-timed current "
                    "acquisition. Use software-timed reads (read_analog) instead."
                )
            channel_list.append(int(channel.physical_channel))
            channel_type_list.append(self._get_analog_channel_type(channel.terminal_config))
            gain_list.append(channel_ranges[channel.alias])
            alias_list.append(channel.alias)

        # Each TC entry must immediately follow its CJC (cold-junction sensor) entry, per MCC's convention:
        tc_channels = sorted(
            (channel for channel in channels if isinstance(channel, AnalogThermocoupleChannel)),
            key=lambda channel: int(channel.physical_channel),
        )
        for channel in tc_channels:
            tc = int(channel.physical_channel)
            # CJC0->TC0, CJC1->TC1/TC2, CJC2->TC3 per bank of 4
            # Tables: files.digilent.com/manuals/USB-1616HS-4.pdf p.18 and /manuals/USB-2527.pdf p.23.
            cjc = 3 * (tc // 4) + (0, 1, 1, 2)[tc % 4]
            channel_list += [cjc, tc]
            channel_type_list += [ChannelType.CJC, ChannelType.TC]
            gain_list += [ULRange.NOTUSED, ULRange.NOTUSED]
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
        # No channel-type pre-validation: DAQICHANTYPE under-reports on some boards, so daq_in_scan is the authority.
        channel_list, channel_type_list, gain_list, aliases = self._build_channel_lists(channels, channel_ranges)
        num_chans = len(channel_list)

        fetch_size = num_chans * hw_timing_config.samples_per_channel

        # Oversize the buffer: (multiplier - 1) extra fetch cycles of overrun tolerance during timing jitter.
        self.buffer_size = fetch_size * buffer_multiplier

        scan_options = ScanOptions.BACKGROUND | ScanOptions.CONTINUOUS
        # HIGHRESRATE interprets the rate argument in samples per 1000 seconds, which sub-1-Hz rates need.
        high_res = hw_timing_config.sample_rate < 1
        if high_res:
            scan_options |= ScanOptions.HIGHRESRATE
        rate = int(hw_timing_config.sample_rate * 1000) if high_res else int(hw_timing_config.sample_rate)

        if self._device_info.ai_resolution <= 16:
            self.memhandle = ul.win_buf_alloc(self.buffer_size)
            self.ctype, self.copy_func = c_ushort, ul.win_buf_to_array
        elif self._device_info.ai_resolution <= 32:
            self.memhandle = ul.win_buf_alloc_32(self.buffer_size)
            self.ctype, self.copy_func = c_ulong, ul.win_buf_to_array_32
        else:
            self.memhandle = ul.win_buf_alloc_64(self.buffer_size)
            self.ctype, self.copy_func = c_ulonglong, ul.win_buf_to_array_64

        if not self.memhandle:
            raise RuntimeError("Failed to allocate memory")

        # If the scan call fails, free the buffer here: stop() is not guaranteed to run for a scan that never started.
        try:
            actual_rate, _, _ = ul.daq_in_scan(
                self._board_num,
                channel_list,
                channel_type_list,
                gain_list,
                num_chans,
                rate,
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
        self.actual_rate = actual_rate / 1000 if high_res else actual_rate

    def convert(self, buffer_snapshot: Array, samples_per_channel: int) -> list[float]:
        """Snapshot -> user-channel engineering units in scan order, with CJC columns dropped."""
        to_eng_units = ul.to_eng_units if self._device_info.ai_resolution <= 16 else ul.to_eng_units_32

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
            err_code, temps = ul.get_tc_values(
                self._board_num,
                self._channel_list,
                self._channel_type_list,
                len(self._channel_list),
                scratch,
                0,
                samples_per_channel,
                get_temp_scale(self._tc_unit),
            )
            # publish NaN for bad scan (this is the only returned error code, everything else raises)
            if err_code != ErrorCode.NOERRORS:
                logger.warning("get_tc_values reported OUTOFRANGE (open or overranged thermocouple), returning NaN")
                return [math.nan if temp == -9999.0 else temp for temp in temps]
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

    def __init__(self, board_num: int, device_info: MCCDeviceInfo):
        self._board_num = board_num
        self._device_info = device_info
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
        # a_in_scan reads a contiguous ascending span; the gain queue narrows it and sets per-channel ranges.
        ordered = sorted(channels, key=lambda channel: int(channel.physical_channel))
        channel_numbers = [int(channel.physical_channel) for channel in ordered]
        gains = [channel_ranges[channel.alias] for channel in ordered]
        num_chans = len(ordered)

        # Every SCALEDATA-capable AI device in the UL catalog also has a channel/gain queue.
        if not self._device_info.ai_supports_gain_queue:
            raise ValueError(
                "This device has no channel/gain queue, so hardware-timed acquisition is not supported "
                "by this driver. Use software-timed reads (read_analog) instead."
            )

        fetch_size = num_chans * hw_timing_config.samples_per_channel

        # Oversize the buffer: (multiplier - 1) extra fetch cycles of overrun tolerance during timing jitter.
        self.buffer_size = fetch_size * buffer_multiplier

        self.memhandle = ul.scaled_win_buf_alloc(self.buffer_size)
        if not self.memhandle:
            raise RuntimeError("Failed to allocate memory")

        scan_options = ScanOptions.BACKGROUND | ScanOptions.CONTINUOUS | ScanOptions.SCALEDATA
        # HIGHRESRATE interprets the rate argument in samples per 1000 seconds, which sub-1-Hz rates need.
        high_res = hw_timing_config.sample_rate < 1
        if high_res:
            scan_options |= ScanOptions.HIGHRESRATE
        rate = int(hw_timing_config.sample_rate * 1000) if high_res else int(hw_timing_config.sample_rate)

        # If the scan call fails, undo the queue and buffer here: stop() only runs for a scan that started.
        try:
            ul.a_load_queue(self._board_num, channel_numbers, gains, num_chans)
            actual_rate = ul.a_in_scan(
                self._board_num,
                channel_numbers[0],
                channel_numbers[-1],
                self.buffer_size,
                rate,
                gains[0],  # ignored: the loaded channel/gain queue defines each channel's range
                self.memhandle,
                scan_options,
            )
        except Exception:
            try:
                ul.a_load_queue(self._board_num, [], [], 0)
            except Exception:
                pass
            try:
                ul.win_buf_free(self.memhandle)
            except Exception:
                pass
            self.memhandle = 0
            raise

        self.scan_width = num_chans
        self.aliases = [channel.alias for channel in ordered]
        self.actual_rate = actual_rate / 1000 if high_res else actual_rate

    def convert(self, buffer_snapshot: Array, samples_per_channel: int) -> list[float]:
        """SCALEDATA already produced engineering units per each channel's configured input type."""
        return list(buffer_snapshot)

    def stop(self) -> None:
        """Stop the background scan, clear the channel/gain queue, and free the scan buffer."""
        try:
            ul.stop_background(self._board_num, self.function_type)
        except Exception:
            pass
        # clear the channel/gain queue so it does not leak into later scans or software reads
        try:
            ul.a_load_queue(self._board_num, [], [], 0)
        except Exception:
            pass
        if self.memhandle:
            try:
                ul.win_buf_free(self.memhandle)
            except Exception:
                pass
            self.memhandle = 0
