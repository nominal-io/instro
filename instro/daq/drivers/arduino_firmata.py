from __future__ import annotations

import queue
import time
import warnings
from typing import TYPE_CHECKING, Any, Mapping

from instro.daq import DAQDriverBase
from instro.daq.types import (
    AnalogChannel,
    DAQChannel,
    DigitalChannel,
    DigitalLineChannel,
    Direction,
    HWTimingConfig,
    Logic,
)
from instro.lib.types import Measurement

if TYPE_CHECKING:
    import pyfirmata2


class ArduinoFirmata(DAQDriverBase):
    """Arduino DAQ driver via Firmata protocol.

    Analog and digital I/O over StandardFirmata. The sampling rate controls how
    often the Arduino sends analog readings back over serial. The Firmata default
    is ~53 Hz (19ms interval). Rates above ~100 Hz may cause dropped messages at
    the default 57600 baud; the actual ceiling depends on baud rate and the number
    of configured channels.

    Physical channel format: analog input "A0-A5"; digital line "D2"-"D13".
    Pin ranges depend on specific Arduino board.
    """

    def __init__(self, port: str, sampling_rate_hz: float = 1000 / 19) -> None:
        super().__init__()
        self._port = port
        if sampling_rate_hz > 1000:
            warnings.warn(
                f"{sampling_rate_hz} Hz exceeds the Firmata protocol maximum of 1000Hz; clamping to 1000 Hz",
                UserWarning,
                stacklevel=2,
            )
            self._sampling_interval_ms = 1

        elif sampling_rate_hz <= 0:
            raise ValueError(f"sampling_rate_hz must be > 0; got {sampling_rate_hz}Hz")
        else:
            self._sampling_interval_ms = int(1000 / sampling_rate_hz)
        self._board: pyfirmata2.Arduino | None = None
        self._pins: dict[str, Any] = {}
        self._latest_values: dict[str, float] = {}
        self._sample_queue: queue.Queue[dict[str, float]] = queue.Queue()
        self._pending_updates: set[str] = set()
        self._expected_ai_channels: frozenset[str] = frozenset()

    def open(self) -> None:
        try:
            import pyfirmata2
        except ImportError as e:
            raise ImportError(
                "pyfirmata2 is required for ArduinoFirmata. Install it with: uv sync --extra arduino"
            ) from e
        self._board = pyfirmata2.Arduino(self._port)
        it = pyfirmata2.util.Iterator(self._board)
        it.start()
        time.sleep(0.1)  # allow the iterator to receive first handshake response

    def close(self) -> None:
        if self._board is not None:
            self._board.exit()
            self._board = None
        self._pins.clear()

    def set_sampling_rate(self, rate_hz: float) -> None:
        """Set the analog sampling rate. Rates above ~100Hz may cause dropped messages at the default 57600 baud."""
        if rate_hz > 1000:
            warnings.warn(
                f"{rate_hz} Hz exceeds the Firmata protocol maximum of 1000 Hz; clamping to 1000 Hz",
                UserWarning,
                stacklevel=2,
            )
            rate_hz = 1000

        elif rate_hz <= 0:
            raise ValueError(f"rate_hz must be > 0; got {rate_hz}Hz")
        self._sampling_interval_ms = int(1000 / rate_hz)
        if self._board is not None:
            self._board.setSamplingInterval(self._sampling_interval_ms)

    def configure_ao_channel(self, channel: AnalogChannel) -> None:
        assert self._board is not None, "Call open() before configuring channels"
        pin_num = _parse_digital_pin(channel.physical_channel)
        pin = self._board.get_pin(f"d:{pin_num}:p")
        self._pins[channel.alias] = pin
        self._ao_channels[channel.alias] = channel

    def write_analog_value(self, channel: AnalogChannel, value: float) -> None:
        normalized = (value - channel.range_min) / (channel.range_max - channel.range_min)
        self._pins[channel.alias].write(max(0.0, min(1.0, normalized)))

    def configure_ai_channel(self, channel: AnalogChannel) -> None:
        assert self._board is not None, "Call open() before configuring channels"
        alias_copy = channel.alias
        pin_num = _parse_analog_pin(channel.physical_channel)
        pin = self._board.get_pin(f"a:{pin_num}:i")
        pin.register_callback(lambda value, a=alias_copy: self._on_analog_callback(a, value))
        pin.enable_reporting()
        self._pins[channel.alias] = pin
        self._ai_channels[channel.alias] = channel

    def configure_ai_hw_timing(self, hw_timing_config: HWTimingConfig) -> None:
        # Firmata timing is managed internally in start()/stop(); do not call configure_ai_sampling_rate() on this driver.
        raise NotImplementedError("ArduinoFirmata does not support hardware-timed buffered acquisition")

    def start(self, **kwargs: Any) -> None:
        if self._board is not None:
            # Set a synthetic timing config so InstroDAQ routes daemon calls through fetch_analog()
            # rather than raising. samples_per_channel=1 because each fetch returns one snapshot.
            # This bypasses the normal configure_ai_hw_timing() path intentionally - Firmata has no
            # hardware timing; start()/stop() own the config lifecycle instead
            sample_rate = 1000 / self._sampling_interval_ms
            self._ai_hw_timing_config = HWTimingConfig(
                sample_rate=sample_rate,
                sample_period=round(1e9 / sample_rate),
                samples_per_channel=1,
            )
            self._expected_ai_channels = frozenset(self._ai_channels.keys())
            self._board.samplingOn(self._sampling_interval_ms)

    def stop(self, **kwargs: Any) -> None:
        if self._board is not None:
            self._board.samplingOff()
        self._ai_hw_timing_config = None
        self._pending_updates.clear()
        self._expected_ai_channels = frozenset()
        while not self._sample_queue.empty():
            try:
                self._sample_queue.get_nowait()
            except queue.Empty:
                break

    def _on_analog_callback(self, alias: str, value: float | None) -> None:
        self._latest_values[alias] = value if value is not None else 0.0
        self._pending_updates.add(alias)
        if self._expected_ai_channels and self._pending_updates >= self._expected_ai_channels:
            self._sample_queue.put(dict(self._latest_values))
            self._pending_updates.clear()

    def read_analog(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for alias in self._ai_channels:
            result[alias] = self._latest_values.get(alias, 0.0)
        return result

    def fetch_analog(self) -> dict[str, float]:
        timeout = max(1.0, self._sampling_interval_ms / 1000 * 3)
        try:
            return self._sample_queue.get(timeout=timeout)
        except queue.Empty:
            return {}

    def _read_to_measurements(
        self,
        response: dict[str, float],
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs: Any,
    ) -> list[Measurement]:
        if not response:
            return []
        timestamp = time.time_ns()
        channel_data: dict[str, list[float]] = {}
        for alias, raw in response.items():
            ch = channel_list.get(alias)
            if not isinstance(ch, AnalogChannel):
                continue
            voltage = ch.range_min + (raw * (ch.range_max - ch.range_min))

            channel_data[f"{daq_name}.{alias}"] = [voltage]
        return [Measurement(channel_data=channel_data, timestamps=[timestamp], tags={**default_tags, **kwargs})]

    def configure_di_line_channel(
        self, physical_channel: str, logic: Logic, logic_level: float | None = None, alias: str | None = None
    ) -> None:
        assert self._board is not None, "Call open() before configuring channels"
        pin_num = _parse_digital_pin(physical_channel)
        pin = self._board.get_pin(f"d:{pin_num}:i")
        key = alias or physical_channel

        alias_copy = key
        pin.register_callback(
            lambda value, a=alias_copy: self._latest_values.__setitem__(a, value if value is not None else 0)
        )
        pin.enable_reporting()
        self._pins[key] = pin
        self._di_channels[key] = DigitalLineChannel(
            physical_channel=physical_channel,
            alias=key,
            direction=Direction.INPUT,
            logic_level=logic_level,
            logic=logic,
            bit_position=pin_num,
        )

    def configure_do_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ) -> None:
        assert self._board is not None, "Call open() before configuring channels"
        pin_num = _parse_digital_pin(physical_channel)
        pin = self._board.get_pin(f"d:{pin_num}:o")
        key = alias or physical_channel
        self._pins[key] = pin
        self._do_channels[key] = DigitalLineChannel(
            physical_channel=physical_channel,
            alias=key,
            direction=Direction.OUTPUT,
            logic_level=logic_level,
            logic=logic,
            bit_position=pin_num,
        )

    def write_digital_line(self, channel: DigitalChannel, data: int) -> None:
        self._pins[channel.alias].write(data)

    def read_digital_line(self, channel: DigitalChannel) -> int:
        raw = self._latest_values.get(channel.alias, 0)
        return int(bool(raw))

    def write_digital_port(self, channel: DigitalChannel, data: int) -> None:
        raise NotImplementedError("ArduinoFirmata does not support port-mode digital I/O")

    def read_digital_port(self, channel: DigitalChannel) -> int:
        raise NotImplementedError("ArduinoFirmata does not support port-mode digital I/O")


def _parse_analog_pin(physical_channel: str) -> int:
    """Parse A0-A5 (Range depends on board model)."""
    return int(physical_channel.lstrip("Aa"))


def _parse_digital_pin(physical_channel: str) -> int:
    """Parse D2-D13 (Range depends on board model) to pyfirmata2 pin index."""
    return int(physical_channel.lstrip("Dd"))
