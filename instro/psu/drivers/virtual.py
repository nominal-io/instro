"""Virtual PSU driver: outputs simulated in-process, with no transport, server, or hardware."""

from __future__ import annotations

import math
import random
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
    voltage_setpoint: float = 0.0
    current_limit: float = 0.0
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

    Each channel drives a resistive load. With the output on, a channel holds its voltage
    setpoint (constant voltage) until the load would draw more than the current limit, then
    holds the current limit (constant current). Readings carry 0.5% noise, the SCPI simulator's
    level. Enabled overvoltage or overcurrent protection trips when the output exceeds its level,
    latching the output off until `clear_protection` and a new `output_enable`. Remote sense and
    the overvoltage protection delay are stored and have no effect on readings. To exercise the
    VISA transport as well, use `SimulatedPSU` with the bundled SCPI simulator.
    """

    def __init__(
        self, num_channels: int = DEFAULT_NUM_CHANNELS, load_resistance: float = DEFAULT_LOAD_RESISTANCE
    ) -> None:
        """Create a virtual PSU.

        Args:
            num_channels (int): Number of output channels, numbered from 1.
            load_resistance (float): Resistance of the load on every channel, in ohms. ``0`` is a
                short and ``math.inf`` an open circuit. Change ``load_resistance`` at any time to
                move a channel between constant voltage and constant current.

        Raises:
            ValueError: ``num_channels`` is less than 1.

        Example::

            from instro.psu import InstroPSU
            from instro.psu.drivers import VirtualPSU

            psu = InstroPSU(name="psu", driver=VirtualPSU(num_channels=1, load_resistance=10.0), num_channels=1)
            with psu:
                psu.apply(current_limit=1.0, voltage=5.0, enable=True, channel=1)
                print(psu.get_current(channel=1).latest)  # about 0.5
                print(psu.get_operating_mode(channel=1).latest)  # CV
        """
        if num_channels < 1:
            raise ValueError(f"num_channels must be at least 1, got {num_channels}")
        self.num_channels = num_channels
        self.load_resistance = load_resistance
        self._channels = {channel: _ChannelState() for channel in range(1, num_channels + 1)}

    def open(self) -> None:
        """Nothing to connect to."""

    def close(self) -> None:
        """Nothing to disconnect."""

    def set_voltage(self, voltage: float, channel: int) -> None:
        self._channel(channel).voltage_setpoint = voltage

    def get_voltage(self, channel: int) -> float:
        voltage, _, _ = self._regulate(channel)
        return _add_noise(voltage)

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        self._channel(channel).current_limit = current_limit

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
        self._channel(channel).overvoltage_protection_level = voltage

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
        self._channel(channel).overcurrent_protection_level = current

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

        resistance = self.load_resistance
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


def _add_noise(value: float) -> float:
    return random.gauss(value, abs(value) * NOISE_PERCENT / 3)
