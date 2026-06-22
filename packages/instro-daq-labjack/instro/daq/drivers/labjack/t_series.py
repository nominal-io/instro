import atexit
import time
import weakref
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Mapping

from instro.daq import DAQDriverBase, HWTimingException
from instro.daq.drivers import HWTimestamper
from instro.daq.drivers.labjack.t_series_models import LJ_T4, LJ_T7, LJ_T8, LJ_Model
from instro.daq.types import (
    AnalogChannel,
    DAQChannel,
    DigitalChannel,
    DigitalLineChannel,
    Direction,
    HWTimingConfig,
    Logic,
)
from instro.lib import Measurement
from labjack import ljm

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

    def __init__(self, device_id: str):
        super().__init__()
        self._model: LJ_Model | None = None
        self._handle: int | None = None
        self._info: tuple[int, int, int, int, int, int] | None = None
        self._device_id = device_id

        # hw timing settings since LabJack has a single timing engine and samples/channel are predefined
        self._global_scan_rate: float | None = None
        self._global_scans_per_read: int | None = None
        self._streaming_active: bool = False
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
        try:
            ljm.eStreamStop(self._handle)
        except ljm.LJMError as e:
            pass

    def stop(self, **kwargs):
        """Stop the DAQ device."""
        if self._streaming_active:
            # TODO add debug logger
            # ljm.eStreamStop(self._handle)
            self._streaming_active = False

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
        """Configure an ai channel on the LabJack device."""
        if self._model is None:
            self._initialize_model()

        assert self._model is not None
        aNames, aValues = self._model.ai_channel_configs(channel)

        if aNames:
            ljm.eWriteNames(self._handle, len(aNames), aNames, aValues)

        self._ai_channels[channel.alias] = channel

    def configure_ao_channel(self, channel: AnalogChannel):
        # LabJack DACs don't need pre-configuration; write_analog_value uses ljm.eWriteName directly.
        # Still record the channel so InstroDAQ's ao_channels proxy can resolve it.
        self._ao_channels[channel.alias] = channel

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
        aNames, aValues = self._model.hw_timing_configs(hw_timing_config=hw_timing_config, channels=ai_channels)

        if aNames:
            ljm.eWriteNames(self._handle, len(aNames), aNames, aValues)

        self._global_scan_rate = hw_timing_config.sample_rate
        self._global_scans_per_read = hw_timing_config.samples_per_channel

        self._ai_hw_timing_config = hw_timing_config

    def _validate_scan_rate(self, hw_timing_config: HWTimingConfig, channels: list[AnalogChannel]):
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

        if self._model.MAX_SCAN_RATE / len(channels) < hw_timing_config.sample_rate:
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

        physical_channels = [ch.physical_channel for ch in channels]

        scan_list = ljm.namesToAddresses(len(physical_channels), physical_channels)[0]

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

        ljm.setStreamCallback(self._handle, self._stream_callback)

        self._streaming_active = True

    def _stream_callback(self, arg):
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

        response = ljm.eReadNames(handle=self._handle, numFrames=len(channels), aNames=physical_channels)
        timestamp = time.time_ns()  # TODO read from labjack. It has some capabilities here.

        return LabJackData(data=response, timestamp=timestamp, dt=None)

    def fetch_analog(
        self,
    ) -> LabJackData:
        # Is receiving data from the ljm registered callback.
        try:
            callback_data = self._data_queue.get(timeout=5)
            labjack_data, timestamp = callback_data[0], callback_data[1]
            samples, self._points_in_fifo, self.points_in_buffer = labjack_data[0], labjack_data[1], labjack_data[2]
            return LabJackData(data=samples, timestamp=timestamp, dt=self._actual_sample_period)
        except Empty:
            raise TimeoutError(f"LabJack timeout. No data received.")

    def get_actual_sample_rate(self) -> float | None:
        return self._actual_sample_rate

    def write_analog_value(self, channel: AnalogChannel, value: float):
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
