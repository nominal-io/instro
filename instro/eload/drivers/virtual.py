"""Virtual E-Load driver: a load and its source simulated in-process, with no transport, server, or hardware."""

from __future__ import annotations

import math
import random

from instro.eload import ELoadDriverBase
from instro.eload.types import LoadMode, SlewRateDirection

NOISE_PERCENT = 0.005


def _add_noise(value: float) -> float:
    return random.gauss(value, abs(value) * NOISE_PERCENT / 3)


def _check_channel(channel: int) -> None:
    if channel != 1:
        raise ValueError(f"VirtualELoad has a single channel, got channel {channel}")


class VirtualELoad(ELoadDriverBase):
    """Single-channel E-Load simulated in-process: no transport, server, or hardware.

    The load sinks from a simulated source: a fixed voltage behind an internal resistance. With the
    input off the terminals read the open-circuit voltage at 0 A; with it on, the readings are the
    operating point the active mode and level set on that source, plus noise (0.5%). As on the
    `BK85XXB`, ``short_output`` also switches the input: a short pulls the terminals to 0 V at the
    source's short-circuit current, and ending it turns the input off. Range and slew-rate settings
    are accepted and have no effect on readings, so any `InstroELoad` call works.

    Levels the source cannot reach are not errors: CC and CR stop at the short-circuit current, and
    CP stops at the source's maximum power (half its open-circuit voltage), where a real load would
    lose regulation. There are no ratings, protections, or time dynamics.
    """

    def __init__(self, source_voltage: float = 12.0, source_resistance: float = 0.5) -> None:
        """Create a virtual E-Load.

        Args:
            source_voltage (float): Open-circuit voltage of the simulated source, in volts. Must not
                be negative.
            source_resistance (float): Internal resistance of the simulated source, in ohms. Must be
                positive; it sets how far the voltage sags as the load draws current.

        Both stay settable as attributes, e.g. to sag the source mid-run, and are validated on
        every set.

        Raises:
            ValueError: If ``source_voltage`` is negative or ``source_resistance`` is not positive.

        Example::

            from instro.eload import InstroELoad, LoadMode
            from instro.eload.drivers import VirtualELoad

            eload = InstroELoad(name="eload", driver=VirtualELoad(source_voltage=12.0, source_resistance=0.5))
            with eload:
                eload.set_mode(LoadMode.CC)
                eload.set_level(2.0)
                eload.output_enable(True)
                print(eload.get_current().latest)  # about 2.0
                print(eload.get_voltage().latest)  # about 11.0
        """
        self.source_voltage = source_voltage
        self.source_resistance = source_resistance
        self._mode = LoadMode.CC
        self._levels: dict[LoadMode, float] = {
            LoadMode.CC: 0.0,
            # above any source, so CV draws nothing until set, however source_voltage changes
            LoadMode.CV: math.inf,
            LoadMode.CP: 0.0,
            LoadMode.CR: 1000.0,
        }
        self._curr_limit: float | None = None
        self._input_enabled = False
        self._shorted = False

    @property
    def source_voltage(self) -> float:
        """Open-circuit voltage of the simulated source, in volts."""
        return self._source_voltage

    @source_voltage.setter
    def source_voltage(self, value: float) -> None:
        if value < 0:
            raise ValueError(f"source_voltage must not be negative, got {value}")
        self._source_voltage = value

    @property
    def source_resistance(self) -> float:
        """Internal resistance of the simulated source, in ohms."""
        return self._source_resistance

    @source_resistance.setter
    def source_resistance(self, value: float) -> None:
        if value <= 0:
            raise ValueError(f"source_resistance must be positive, got {value}")
        self._source_resistance = value

    def open(self) -> None:
        """Nothing to connect to."""

    def close(self) -> None:
        """Nothing to disconnect."""

    def short_output(self, enable: bool, channel: int) -> None:
        """Start or end a short; like the `BK85XXB`, this also turns the input on or off.

        The short stays set until ``short_output(False)``: turning the input off and on again with
        ``output_enable`` leaves it shorted, as ``INPut`` alone leaves ``INPut:SHORt`` on the BK85XXB.
        """
        _check_channel(channel)
        self._shorted = enable
        self._input_enabled = enable

    def set_mode(self, mode: LoadMode, channel: int) -> None:
        _check_channel(channel)
        self._mode = mode

    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        """Set ``mode``'s level; each mode keeps its own. ``curr_limit`` caps the current in CV mode."""
        _check_channel(channel)
        if value < 0:
            raise ValueError(f"{mode.value} level must be non-negative, got {value}")
        if curr_limit is not None and curr_limit < 0:
            raise ValueError(f"curr_limit must be non-negative, got {curr_limit}")
        self._levels[mode] = value
        if mode is LoadMode.CV:
            self._curr_limit = curr_limit

    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        """Accepted; no effect on readings."""
        _check_channel(channel)

    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        """Accepted; no effect on readings, which settle instantly."""
        _check_channel(channel)

    def output_enable(self, enable: bool, channel: int) -> None:
        """Turn the input on or off; a short set by `short_output` stays set."""
        _check_channel(channel)
        self._input_enabled = enable

    def get_current(self, channel: int) -> float:
        _check_channel(channel)
        return _add_noise(self._operating_point()[1])

    def get_voltage(self, channel: int) -> float:
        _check_channel(channel)
        return _add_noise(self._operating_point()[0])

    def _operating_point(self) -> tuple[float, float]:
        """Return the noise-free ``(volts, amperes)`` at the terminals."""
        vs, rs = self.source_voltage, self.source_resistance
        short_circuit_current = vs / rs
        if not self._input_enabled:
            return vs, 0.0
        if self._shorted:
            return 0.0, short_circuit_current

        level = self._levels[self._mode]
        if self._mode is LoadMode.CC:
            current = min(level, short_circuit_current)
        elif self._mode is LoadMode.CR:
            current = vs / (rs + level)
        elif self._mode is LoadMode.CV:
            current = max(vs - level, 0.0) / rs
            if self._curr_limit is not None:
                current = min(current, self._curr_limit)
        else:
            # V * I = P with V = vs - I * rs; past the source's maximum power, sit at the maximum-power point
            discriminant = vs * vs - 4 * rs * level
            current = (vs - math.sqrt(discriminant)) / (2 * rs) if discriminant >= 0 else vs / (2 * rs)
        return vs - current * rs, current
