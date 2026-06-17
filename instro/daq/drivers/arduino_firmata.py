from __future__ import annotations

import time
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

    Arduino UNO:
        Physical channel format - analog input: ''"A0"''-''"A5"'';
        digital line: ''"D2"'' - ''"D13"''

    Pin ranges depend on specific Arduino board.

    """

    def __init__(self, port: str, sampling_interval_ms: int = 19) -> None:
        super().__init__()
        self._port = port
        self._sampling_interval_ms = sampling_interval_ms
        self._board: pyfirmata2.Arduino | None = None
        self._pins: dict[str, Any] = {}
        self._latest_values: dict[str, float] = {}

    def open(self) -> None:
        try:
            import pyfirmata2
        except ImportError as e:
            raise ImportError(
                "pyfirmata2 is required for ArduinoFirmata.install it with: uv sync --extra arduino"
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

    def configure_ai_channel(self, channel: AnalogChannel) -> None:
        assert self._board is not None, "Call open() before configuring channels"
        alias_copy = channel.alias
        pin_num = _parse_analog_pin(channel.physical_channel)  # turns A0 into 0, A3 into 3, etc
        pin = self._board.get_pin(f"a:{pin_num}:i")
        pin.register_callback(
            lambda value, a=alias_copy: self._latest_values.__setitem__(a, value if value is not None else 0.0)
        )
        pin.enable_reporting()  # tells arduino to continuously send readings of this pin
        self._pins[channel.alias] = pin  # store pin object so read_analog can find by alias
        self._ai_channels[channel.alias] = channel

    def configure_ai_hw_timing(self, hw_timing_config: HWTimingConfig) -> None:
        raise NotImplementedError("ArduinoFirmata does not support hardware-timed buffered acquisition")

    def start(self, **kwargs: Any) -> None:
        if self._board is not None:
            self._board.samplingOn(self._sampling_interval_ms)

    def stop(self, **kwargs: Any) -> None:
        if self._board is not None:
            self._board.samplingOff()

    def read_analog(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for alias in self._ai_channels:
            result[alias] = self._latest_values.get(alias, 0.0)
        return result

    def fetch_analog(self) -> Any:
        raise NotImplementedError(
            "ArduinoFirmata does not support hardware-timed buffered fetch; use software-timed read_analog()"
        )

    def _read_to_measurements(
        self,
        response: dict[str, float],
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs: Any,
    ) -> list[Measurement]:
        timestamp = time.time_ns()
        channel_data: dict[str, list[float]] = {}
        for alias, raw in response.items():
            ch = channel_list.get(alias)
            if not isinstance(ch, AnalogChannel):
                continue
            voltage = ch.range_min + (
                raw * (ch.range_max - ch.range_min)
            )  # if raw is in range 0-1, else we must change

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
