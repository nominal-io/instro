"""Virtual PSU driver: outputs simulated in-process, with no transport, server, or hardware."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass

from instro.psu import OperatingMode, PSUDriverBase

# The SCPI simulator's defaults (instro.psu.scpi_sim_server), so both simulated PSUs behave alike.
# Copied rather than imported: that module pulls in its terminal UI, too heavy for every
# `import instro.psu.drivers`.
DEFAULT_NUM_CHANNELS = 2
DEFAULT_LOAD_RESISTANCE = 1000.0
DEFAULT_VOLTAGE_MAX = 60.0
DEFAULT_CURRENT_MAX = 10.0
NOISE_PERCENT = 0.005


@dataclass
class _ChannelState:
    load_resistance: float = DEFAULT_LOAD_RESISTANCE
    voltage_setpoint: float = 0.0
    # unlike the SCPI simulator's 0 A, so setting a voltage and enabling the output is enough
    current_limit: float = DEFAULT_CURRENT_MAX
    output_enabled: bool = False
    overvoltage_protection_level: float = DEFAULT_VOLTAGE_MAX
    overvoltage_protection_enabled: bool = False
    overvoltage_protection_delay: float = 0.0
    overcurrent_protection_level: float = DEFAULT_CURRENT_MAX
    overcurrent_protection_enabled: bool = False
    overvoltage_protection_tripped: bool = False
    overcurrent_protection_tripped: bool = False
    remote_sense_enabled: bool = False


class VirtualPSU(PSUDriverBase):
    """PSU simulated in-process: no transport, server, or hardware.

    Each channel drives its own resistive load. With the output on, a channel holds its voltage
    setpoint (constant voltage) until the load would draw more than the current limit, then
    holds the current limit (constant current). Readings carry 0.5% noise, the SCPI simulator's
    level. The current limit starts at the channel maximum (10 A), so setting a voltage and
    enabling the output is enough to see it.

    As on the SCPI simulator, voltages must lie within 0 to 60 V and currents within 0 to 10 A,
    or the setter raises ``ValueError``. Unlike it, a setpoint may sit above its protection level,
    which is how a virtual channel trips: there is no external source to push the output past
    its setpoint.

    Enabled overvoltage or overcurrent protection trips when the output exceeds its level,
    latching the output off until `clear_protection` and a new `output_enable`; ``output_enable``
    alone also clears the latch, as on the SCPI simulator. Protection is evaluated whenever a
    reading is taken (voltage, current, mode, output status, or a trip state), not continuously,
    so a fault removed before the next reading never trips. `InstroPSU`'s background polling
    reads every channel, which keeps the check current. Remote sense and the overvoltage
    protection delay are stored and have no effect on readings. To exercise the VISA transport
    as well, use `SimulatedPSU` with the bundled SCPI simulator.
    """

    def __init__(
        self,
        num_channels: int = DEFAULT_NUM_CHANNELS,
        load_resistance: float | Mapping[int, float] = DEFAULT_LOAD_RESISTANCE,
    ) -> None:
        """Create a virtual PSU.

        Args:
            num_channels (int): Number of output channels, numbered from 1. Pass the same count
                to `InstroPSU`; a channel above it raises ``ValueError``.
            load_resistance (float | Mapping[int, float]): Resistance of each channel's load, in
                ohms: one value for every channel, or a mapping from channel to value, with
                channels left out at 1000 ohms. ``0`` is a short and ``math.inf`` an open circuit.
                Change it at any time with `set_load_resistance` to move a channel between
                constant voltage and constant current.

        Raises:
            ValueError: ``num_channels`` is less than 1, or ``load_resistance`` is negative or
                names a channel that does not exist.

        Example::

            from instro.psu import InstroPSU
            from instro.psu.drivers import VirtualPSU

            driver = VirtualPSU(num_channels=2, load_resistance={1: 10.0, 2: 1.0})
            with InstroPSU(name="psu", driver=driver, num_channels=2) as psu:
                for channel in (1, 2):
                    psu.apply(current_limit=1.0, voltage=5.0, enable=True, channel=channel)
                print(psu.get_operating_mode(channel=1).latest)  # CV: 10 ohms draws 0.5 A
                print(psu.get_operating_mode(channel=2).latest)  # CC: 1 ohm would draw 5 A
                driver.set_load_resistance(100.0, channel=2)
                print(psu.get_operating_mode(channel=2).latest)  # CV
        """
        if num_channels < 1:
            raise ValueError(f"num_channels must be at least 1, got {num_channels}")
        self.num_channels = num_channels
        self._channels = {channel: _ChannelState() for channel in range(1, num_channels + 1)}
        if isinstance(load_resistance, Mapping):
            for channel, resistance in load_resistance.items():
                self.set_load_resistance(resistance, channel=channel)
        else:
            self.set_load_resistance(load_resistance)

    def set_load_resistance(self, resistance: float, channel: int | None = None) -> None:
        """Set the load on ``channel``, or on every channel when ``channel`` is ``None``.

        Args:
            resistance (float): Load resistance in ohms. ``0`` is a short and ``math.inf`` an
                open circuit.
            channel (int | None): Target channel (1-indexed), or ``None`` for every channel.

        Raises:
            ValueError: ``resistance`` is negative, or ``channel`` does not exist.
        """
        if not resistance >= 0:
            raise ValueError(f"load_resistance must not be negative, got {resistance}")
        channels = self._channels.values() if channel is None else [self._channel(channel)]
        for state in channels:
            state.load_resistance = resistance

    def get_load_resistance(self, channel: int) -> float:
        """Return the load resistance on ``channel``, in ohms."""
        return self._channel(channel).load_resistance

    def open(self) -> None:
        """Nothing to connect to."""

    def close(self) -> None:
        """Nothing to disconnect."""

    def set_voltage(self, voltage: float, channel: int) -> None:
        self._channel(channel).voltage_setpoint = _check_range("voltage", voltage, DEFAULT_VOLTAGE_MAX, "V")

    def get_voltage(self, channel: int) -> float:
        voltage, _, _ = self._regulate(channel)
        return _add_noise(voltage)

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        self._channel(channel).current_limit = _check_range("current_limit", current_limit, DEFAULT_CURRENT_MAX, "A")

    def get_current(self, channel: int) -> float:
        _, current, _ = self._regulate(channel)
        return _add_noise(current)

    def output_enable(self, enable: bool, channel: int) -> None:
        state = self._channel(channel)
        if enable:
            state.overvoltage_protection_tripped = False
            state.overcurrent_protection_tripped = False
        state.output_enabled = enable

    def get_output_status(self, channel: int) -> bool:
        self._regulate(channel)
        return self._channel(channel).output_enabled

    def get_voltage_setpoint(self, channel: int) -> float:
        return self._channel(channel).voltage_setpoint

    def get_current_setpoint(self, channel: int) -> float:
        return self._channel(channel).current_limit

    def get_operating_mode(self, channel: int) -> OperatingMode:
        _, _, mode = self._regulate(channel)
        return mode

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        level = _check_range("overvoltage protection level", voltage, DEFAULT_VOLTAGE_MAX, "V")
        self._channel(channel).overvoltage_protection_level = level

    def get_overvoltage_protection_level(self, channel: int) -> float:
        return self._channel(channel).overvoltage_protection_level

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._channel(channel).overvoltage_protection_enabled = enabled

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        return self._channel(channel).overvoltage_protection_enabled

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        """Stored; protection trips without delay."""
        self._channel(channel).overvoltage_protection_delay = delay

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        return self._channel(channel).overvoltage_protection_delay

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        level = _check_range("overcurrent protection level", current, DEFAULT_CURRENT_MAX, "A")
        self._channel(channel).overcurrent_protection_level = level

    def get_overcurrent_protection_level(self, channel: int) -> float:
        return self._channel(channel).overcurrent_protection_level

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._channel(channel).overcurrent_protection_enabled = enabled

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        return self._channel(channel).overcurrent_protection_enabled

    def clear_protection(self, channel: int) -> None:
        state = self._channel(channel)
        state.overvoltage_protection_tripped = False
        state.overcurrent_protection_tripped = False

    def get_overvoltage_protection_tripped(self, channel: int) -> bool:
        self._regulate(channel)
        return self._channel(channel).overvoltage_protection_tripped

    def get_overcurrent_protection_tripped(self, channel: int) -> bool:
        self._regulate(channel)
        return self._channel(channel).overcurrent_protection_tripped

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        """Stored; the virtual load has no lead resistance, so readings are unchanged."""
        self._channel(channel).remote_sense_enabled = enabled

    def get_remote_sense_enabled(self, channel: int) -> bool:
        return self._channel(channel).remote_sense_enabled

    def _channel(self, channel: int) -> _ChannelState:
        if channel not in self._channels:
            raise ValueError(f"VirtualPSU has channels 1 to {self.num_channels}, got {channel}")
        return self._channels[channel]

    def _regulate(self, channel: int) -> tuple[float, float, OperatingMode]:
        """Return the noise-free ``(voltage, current, mode)`` on ``channel``, tripping protection first."""
        state = self._channel(channel)
        if not state.output_enabled:
            return 0.0, 0.0, OperatingMode.OFF

        resistance = state.load_resistance
        if resistance == 0:
            demand = math.inf if state.voltage_setpoint > 0 else 0.0
        else:
            demand = state.voltage_setpoint / resistance
        if demand <= state.current_limit:
            voltage, current, mode = state.voltage_setpoint, demand, OperatingMode.CONSTANT_VOLTAGE
        else:
            current, mode = state.current_limit, OperatingMode.CONSTANT_CURRENT
            voltage = current * resistance

        if state.overvoltage_protection_enabled and voltage > state.overvoltage_protection_level:
            state.overvoltage_protection_tripped = True
        elif state.overcurrent_protection_enabled and current > state.overcurrent_protection_level:
            state.overcurrent_protection_tripped = True
        else:
            return voltage, current, mode
        state.output_enabled = False
        return 0.0, 0.0, OperatingMode.OFF


def _check_range(name: str, value: float, maximum: float, unit: str) -> float:
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be within 0 to {maximum:g} {unit}, got {value}")
    return value


def _add_noise(value: float) -> float:
    return random.gauss(value, abs(value) * NOISE_PERCENT / 3)
