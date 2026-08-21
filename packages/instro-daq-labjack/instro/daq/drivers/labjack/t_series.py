import atexit
import logging
import math
import threading
import time
import weakref
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Mapping

from instro.daq import DAQDriverBase, HWTimingException
from instro.daq.drivers import HWTimestamper
from instro.daq.drivers.labjack.t_series_models import LJ_T4, LJ_T7, LJ_T8, LJ_Model
from instro.daq.scaling.thermocouple import kelvin_to_unit, unit_to_kelvin
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
    Direction,
    HWTimingConfig,
    Logic,
)
from instro.lib import Measurement
from labjack import ljm
from labjack.ljm import errorcodes

logger = logging.getLogger(__name__)

# TODO(INSTRO-89): Remove this once context managers are added.
# We use a callback functionality of the LJM driver. This is for performance reasons vs. python threading.
# Registering this python callback to the c library
# can create python segmentation faults when the python interpreter is shutting down.
# Create a weak reference to LabJackData, create a shutdown method, and register it to python's
# atexit feature to explicitly close the ljm driver reference.
_ACTIVE = weakref.WeakSet()


def _panic_shutdown(active=_ACTIVE, library=ljm):
    for obj in list(active):
        try:
            library.close(obj._handle)
        except Exception:
            pass


atexit.register(_panic_shutdown)


@dataclass
class LabJackData:
    data: list[float]
    timestamp: int
    dt: int | None


class LabJackTSeriesDriver(DAQDriverBase):
    """LabJack T-series DAQ driver (T4/T7/T8 via the LJM library)."""

    def __init__(self, device_id: str, stream_buffer_bytes: int = 0):
        """Construct the driver.

        Args:
            device_id: Device serial number, or "ANY" for the first device found.
            stream_buffer_bytes: Device-side stream buffer size, always written when AI hardware
                timing is configured so no value carries over from an earlier session.
                0 selects the device's own default.
                Must be a power of 2 and the max is 262144.
                Setting this prevents the device from dropping scans at high sample rates, which LJM reports as -9999 sample values.
        """
        super().__init__()
        self._model: LJ_Model | None = None
        self._handle: int | None = None
        self._info: tuple[int, int, int, int, int, int] | None = None
        self._device_id = device_id
        self._stream_buffer_bytes = stream_buffer_bytes

        # hw timing settings since LabJack has a single timing engine and samples/channel are predefined
        self._global_scan_rate: float | None = None
        self._global_scans_per_read: int | None = None
        self._streaming_active: bool = False
        self._stream_lock = threading.Lock()  # orders the LJM callback's read against stop()
        self._actual_sample_period: int | None = None
        self._actual_sample_rate: float | None = None
        self._timestamper: HWTimestamper | None = None  # None until first hw-timed read

        self._data_queue: Queue = Queue()

        _ACTIVE.add(self)

    def open(self):
        """Connect to LabJack device."""
        try:
            self._handle = ljm.openS("ANY", "ANY", self._device_id)
            self._info = ljm.getHandleInfo(self._handle)
        except ljm.LJMError as e:
            raise RuntimeError(f"Failed to connect to LabJack device: {e}")

        self._stop_stream()

    def _stop_stream(self):
        """Stop the stream, tolerating a handle with no stream running."""
        try:
            ljm.eStreamStop(self._handle)
        except ljm.LJMError as error:
            logger.debug("eStreamStop on handle %s reported: %s", self._handle, error)

    def stop(self, **kwargs):
        """Stop the DAQ device."""
        with self._stream_lock:
            if not self._streaming_active:
                return
            self._streaming_active = False
        # Released before unregistering: LJM waits on an in-flight callback, which wants this lock.
        ljm.setStreamCallback(self._handle, None)
        self._stop_stream()
        self._timestamper = None
        self._drain_stream_queue()

    def _drain_stream_queue(self):
        """Discard scans the callback queued before the stream stopped."""
        while True:
            try:
                self._data_queue.get_nowait()
            except Empty:
                break

    def close(self):
        """Disconnect from LabJack device."""
        if self._handle is not None:
            ljm.close(self._handle)
            self._handle = None
            self._info = None

    def get_info(self) -> tuple[int, int, int, int, int, int]:
        """Get the LabJack device info."""
        if self._info is None:
            raise RuntimeError("Device not connected")
        return self._info

    def _initialize_model(self):
        """Initialize the LabJack model based on device info."""
        assert self._info is not None
        # Grab device specific behaviors
        match self._info[0]:
            case ljm.constants.dtT4:
                self._model = LJ_T4()
            case ljm.constants.dtT7:
                self._model = LJ_T7()
            case ljm.constants.dtT8:
                self._model = LJ_T8()
            case _:
                raise RuntimeError(f"Unsupported LabJack device type: {self._info[0]}")

    def configure_ai_channel(
        self,
        channel: AnalogChannel,
    ):
        """Deprecated: use ``configure_ai_voltage_channel``. Configures an ai channel on the LabJack device."""
        self.configure_ai_voltage_channel(
            AnalogVoltageChannel(
                physical_channel=channel.physical_channel,
                alias=channel.alias,
                direction=channel.direction,
                range_max=channel.range_max,
                range_min=channel.range_min,
                scaler=channel.scaler,
                terminal_config=channel.terminal_config,
            )
        )

    def configure_ao_channel(self, channel: AnalogChannel):
        """Deprecated: use ``configure_ao_voltage_channel``. Configures an AO channel on the LabJack device."""
        self.configure_ao_voltage_channel(
            AnalogVoltageChannel(
                physical_channel=channel.physical_channel,
                alias=channel.alias,
                direction=channel.direction,
                range_max=channel.range_max,
                range_min=channel.range_min,
                scaler=channel.scaler,
                terminal_config=channel.terminal_config,
            )
        )

    def configure_ai_voltage_channel(self, channel: AnalogVoltageChannel):
        """Configure a voltage ai channel on the LabJack device."""
        if self._model is None:
            self._initialize_model()

        assert self._model is not None
        aNames, aValues = self._model.ai_channel_configs(channel)

        if aNames:
            ljm.eWriteNames(self._handle, len(aNames), aNames, aValues)

        self._ai_channels[channel.alias] = channel

    def configure_ao_voltage_channel(self, channel: AnalogVoltageChannel):
        """Configure a voltage AO channel on the LabJack device."""
        # LabJack DACs don't need pre-configuration; write_analog_value uses ljm.eWriteName directly.
        # Still record the channel so InstroDAQ's ao_channels proxy can resolve it.
        self._ao_channels[channel.alias] = channel

    def configure_ai_current_channel(self, channel: AnalogCurrentChannel):
        raise NotImplementedError(
            "LabJack T-series analog inputs measure voltage only. Measure current through an external shunt "
            "resistor (e.g. an LJTick-CurrentShunt) with a voltage input and a scaler. "
            "See https://support.labjack.com/docs/measuring-current-app-note."
        )

    def configure_ao_current_channel(self, channel: AnalogCurrentChannel):
        raise NotImplementedError(
            "LabJack T-series DACs output voltage only; current output requires external circuitry. "
        )

    def configure_ai_thermocouple_channel(self, channel: AnalogThermocoupleChannel):
        """Configure a thermocouple ai channel; volts convert to temperature in the channel's unit on read.

        ``range_min``/``range_max`` are ignored: the ADC range is fixed per model (T7 0.1 V, T8 0.075 V, T4 none).
        """
        if self._model is None:
            self._initialize_model()
        assert self._model is not None

        if channel.cjc_source is CJCSource.CHANNEL:
            raise ValueError("cjc_source CHANNEL is not supported by the LabJack driver; use INTERNAL or CONSTANT.")
        if channel.cjc_source is CJCSource.CONSTANT and channel.cjc_temp is None:
            raise ValueError("cjc_temp is required when cjc_source is CONSTANT.")

        aNames, aValues = self._model.thermocouple_channel_configs(channel)

        if aNames:
            ljm.eWriteNames(self._handle, len(aNames), aNames, aValues)

        self._ai_channels[channel.alias] = channel

        self._refresh_tc_cjc()

    def _cjc_read_names(self) -> list[str]:
        """CJC sensor registers appended after the AI channels in every read and stream scan list."""
        names: list[str] = []
        for channel in self._ai_channels.values():
            if isinstance(channel, AnalogThermocoupleChannel) and channel.cjc_source is not CJCSource.CONSTANT:
                assert self._model is not None
                name = self._model.tc_cjc_read_name(channel.physical_channel)
                if name is not None and name not in names:
                    names.append(name)
        return names

    def _refresh_tc_cjc(self):
        """Let the model refresh its CJC state when any channel needs device-sourced CJC; no-op otherwise."""
        if any(
            isinstance(channel, AnalogThermocoupleChannel) and channel.cjc_source is not CJCSource.CONSTANT
            for channel in self._ai_channels.values()
        ):
            assert self._model is not None
            self._model.refresh_tc_cjc(self._handle)

    def _tc_volts_to_temps(
        self,
        channel: AnalogThermocoupleChannel,
        volts: list[float],
        cjc_samples: dict[str, list[float]],
    ) -> list[float]:
        """Convert a batch of thermocouple volts to temperatures in the channel's unit."""
        assert self._model is not None
        input_scaler = channel.tc_input_scaler or self._model.default_tc_input_scaler
        if input_scaler is not None:
            volts = [input_scaler.scale(v) for v in volts]

        if channel.cjc_source is CJCSource.CONSTANT:
            assert channel.cjc_temp is not None
            cjc_k = unit_to_kelvin(channel.cjc_temp, channel.unit)
        else:
            cjc_k = self._model.tc_cjc_kelvin(channel.physical_channel, cjc_samples)

        tc_type = getattr(ljm.constants, f"tt{channel.tc_type.value}")
        temps = []
        for v in volts:
            try:
                # Convert calculated Kelvin temp to user requested unit
                # LJM's conversion and the T-series CJC registers work only in kelvin.
                temps.append(kelvin_to_unit(ljm.tcVoltsToTemp(tc_type, v, cjc_k), channel.unit))
            except ljm.LJMError as error:
                if error.errorCode not in (errorcodes.VOLTAGE_OUT_OF_RANGE, errorcodes.TEMPERATURE_OUT_OF_RANGE):
                    raise
                logger.warning("Thermocouple channel '%s' read out of range, returning NaN: %s", channel.alias, error)
                temps.append(math.nan)  # open/overranged input or dropped-scan sentinel; keep the batch flowing
        return temps

    def configure_ai_hw_timing(
        self,
        hw_timing_config: HWTimingConfig,
    ):
        """Configure hardware timing for the specified channels."""
        # Labjack sample rate and samples per channel are configured when the stream is started.
        # We'll use the first channel's hw timing to set the global scan rate and samples per channel
        # and check that all channels and any subsequent calls to configure_hw_timing have the same values.

        ai_channels = list(self._ai_channels.values())
        self._validate_scan_rate(hw_timing_config, ai_channels)

        # Here, we'll configure some of the settling and resolution settings specific to streams
        # We should expand this to expose other register configurations in later versions.
        assert self._model
        aNames, aValues = self._model.hw_timing_configs(
            hw_timing_config=hw_timing_config,
            channels=ai_channels,
            stream_buffer_bytes=self._stream_buffer_bytes,
        )

        if aNames:
            ljm.eWriteNames(self._handle, len(aNames), aNames, aValues)

        self._global_scan_rate = hw_timing_config.sample_rate
        self._global_scans_per_read = hw_timing_config.samples_per_channel

        self._ai_hw_timing_config = hw_timing_config

    def _validate_scan_rate(self, hw_timing_config: HWTimingConfig, channels: list[AnalogChannelUnion]):
        """Pre-check the requested scan rate so we raise a clear error instead of LJM's cryptic one."""
        assert self._model

        # Check absolute scan rate
        if not (self._model.MIN_SCAN_RATE <= hw_timing_config.sample_rate <= self._model.MAX_SCAN_RATE):
            raise HWTimingException(
                f"The requested sample rate is unsupported by the hardware. Valid sample rates are between {self._model.MIN_SCAN_RATE}Hz and {self._model.MAX_SCAN_RATE}Hz."
            )

        # Catch multiplexed scan rate conflicts
        if isinstance(self._model, (LJ_T8)):
            return

        num_scan_columns = len(channels) + len(self._cjc_read_names())
        if self._model.MAX_SCAN_RATE / num_scan_columns < hw_timing_config.sample_rate:
            raise HWTimingException(
                "The requested sample rate is higher than the device can support for the number of channels requested. This is a multiplexed DAQ."
            )

    def start(self, **kwargs):
        """Start the DAQ device for hw timed data acquisition."""
        if self._global_scan_rate is None:
            raise HWTimingException("No hardware timing configuration exists. Can not call Start")

        if self._streaming_active is True:
            # TODO add debug logger
            return

        # For LabJack, we need to know the channels to start streaming
        channels = self._ai_channels.values()
        if not channels:
            raise ValueError("No channels specified to start streaming on LabJack device.")

        scan_names = [ch.physical_channel for ch in channels] + self._cjc_read_names()

        scan_list = ljm.namesToAddresses(len(scan_names), scan_names)[0]

        # Models with no streamable CJC source snapshot it now, before the stream claims the ADC.
        self._refresh_tc_cjc()

        self._timestamper = None
        actual_scan_rate = ljm.eStreamStart(
            self._handle,
            self._global_scans_per_read,
            len(scan_list),
            scan_list,
            self._global_scan_rate,
        )
        self._actual_sample_rate = actual_scan_rate
        self._actual_sample_period = round(1e9 / actual_scan_rate)

        with self._stream_lock:
            self._streaming_active = True

        try:
            ljm.setStreamCallback(self._handle, self._stream_callback)
        except ljm.LJMError:
            with self._stream_lock:
                self._streaming_active = False
            self._stop_stream()
            raise

    def _stream_callback(self, arg):
        # The lock keeps this read strictly before stop()'s eStreamStop, so it cannot fail.
        with self._stream_lock:
            if not self._streaming_active:
                return
            response = ljm.eStreamRead(self._handle)
        ai_timestamp = time.time_ns()  # TODO read from labjack. It has some capabilities here.

        self._data_queue.put_nowait((response, ai_timestamp))

    def read_analog(
        self,
    ) -> LabJackData:
        """Read from analog input channels."""
        channels = self._ai_channels
        physical_channels = [ch.physical_channel for ch in channels.values()]

        # Append _CAPTURE to channel names (except first) to ensure simultaneous sampling from T8
        if isinstance(self._model, (LJ_T8)):
            if len(physical_channels) > 1:
                physical_channels = [physical_channels[0]] + [f"{ch}_CAPTURE" for ch in physical_channels[1:]]

        read_names = physical_channels + self._cjc_read_names()
        self._refresh_tc_cjc()
        response = ljm.eReadNames(handle=self._handle, numFrames=len(read_names), aNames=read_names)
        timestamp = time.time_ns()  # TODO read from labjack. It has some capabilities here.

        return LabJackData(data=response, timestamp=timestamp, dt=None)

    def fetch_analog(
        self,
    ) -> LabJackData:
        if not self._streaming_active:
            raise RuntimeError("No active scan. Call start() before fetch_analog().")
        # Is receiving data from the ljm registered callback.
        try:
            callback_data = self._data_queue.get(timeout=5)
            labjack_data, timestamp = callback_data[0], callback_data[1]
            samples, self._points_in_fifo, self.points_in_buffer = labjack_data[0], labjack_data[1], labjack_data[2]
            return LabJackData(data=samples, timestamp=timestamp, dt=self._actual_sample_period)
        except Empty:
            raise TimeoutError("LabJack timeout. No data received.")

    def get_actual_sample_rate(self) -> float | None:
        return self._actual_sample_rate

    def write_analog_value(self, channel: AnalogChannelUnion, value: float):
        ljm.eWriteName(self._handle, channel.physical_channel, value)

    # ====== DIGITAL ==========

    def configure_di_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        if self._model is None:
            self._initialize_model()

        channel = DigitalLineChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=Direction.INPUT,
            logic_level=logic_level,  # type: ignore
            logic=logic,
        )
        self._di_channels[channel.alias] = channel

    def configure_do_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        if self._model is None:
            self._initialize_model()

        # If the FIO/EIO line is an analog input, it needs to first be changed to a
        # digital I/O by reading from the line or setting it to digital I/O with the
        # DIO_ANALOG_ENABLE register.
        ljm.eReadName(self._handle, physical_channel)

        channel = DigitalLineChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=Direction.OUTPUT,
            logic_level=logic_level,  # type: ignore
            logic=logic,
        )
        self._do_channels[channel.alias] = channel

    def write_digital_line(self, channel: DigitalChannel, data: int):
        if channel.logic is Logic.LOW:
            data = 1 - data
        ljm.eWriteName(self._handle, channel.physical_channel, data)

    def read_digital_line(self, channel: DigitalChannel) -> int:
        response = ljm.eReadName(self._handle, channel.physical_channel)
        if channel.logic is Logic.LOW:
            response = 1 - response

        return int(response)

    def write_digital_port(self, channel: DigitalChannel, data: int):
        raise NotImplementedError("write_digital_port is not yet implemented for LabJack.")

    def read_digital_port(self, channel: DigitalChannel) -> int:
        raise NotImplementedError("read_digital_port is not yet implemented for LabJack.")

    def _read_to_measurements(
        self,
        response: LabJackData,
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs,
    ) -> list[Measurement]:
        # LabJack returns interleaved data.
        # For example, when streaming two channels, AIN0 and AIN1, aData will look like this:
        # aData[0] contains the first AIN0 sample aData[1] contains the first AIN1 sample
        # aData[2] contains the second AIN0 sample aData[3] contains the second AIN1 sample ...

        num_channels = len(channel_list)
        cjc_names = self._cjc_read_names()
        num_columns = num_channels + len(cjc_names)
        samples_per_channel = len(response.data) // num_columns

        # De-interleave the data
        channel_data = {}
        for i, channel in enumerate(channel_list):
            channel_data[f"{daq_name}.{channel}"] = response.data[i::num_columns]

        cjc_samples = {name: response.data[num_channels + i :: num_columns] for i, name in enumerate(cjc_names)}
        for alias, channel_config in channel_list.items():
            if isinstance(channel_config, AnalogThermocoupleChannel):
                key = f"{daq_name}.{alias}"
                channel_data[key] = self._tc_volts_to_temps(channel_config, channel_data[key], cjc_samples)

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
