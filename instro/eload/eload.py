"""Electronic-load (E-Load) instrument interface and driver contract."""

from __future__ import annotations

import abc
import logging
import threading
import time
from typing import Callable

from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher

logger = logging.getLogger(__name__)


class ELoadDriverBase(abc.ABC):
    """Vendor E-Load driver contract. Concrete drivers own their transport and lifecycle."""

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport and put the load in remote mode."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport. Idempotent."""

    @abc.abstractmethod
    def short_output(self, enable: bool, channel: int) -> None:
        """Apply or release the load's short on ``channel``. Takes effect immediately."""

    @abc.abstractmethod
    def set_mode(self, mode: LoadMode, channel: int) -> None:
        """Set the operating mode on ``channel``: CC, CV, CP, or CR.

        Not every model supports every mode; drivers should raise
        ``NotImplementedError`` for unsupported combinations.
        """

    @abc.abstractmethod
    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        """Set the operating level on ``channel`` in the units appropriate for ``mode``.

        Args:
            mode: Active load mode — caller passes it explicitly so the driver
                doesn't need to track state. Units: CC → amperes, CV → volts,
                CP → watts, CR → ohms.
            value: Operating level in ``mode``'s units.
            channel: Target channel (1-indexed).
            curr_limit: Optional current limit; only meaningful in CV mode.
                Drivers may ignore it in other modes.
        """

    @abc.abstractmethod
    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        """Set the operating range on ``channel`` from the expected maximum.

        ``value`` is the maximum operating value in ``mode``'s units (see
        :meth:`set_level`). Drivers typically snap to the closest hardware
        range that covers ``value``.
        """

    @abc.abstractmethod
    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        """Set the per-edge current slew rate on ``channel``.

        Args:
            direction: ``RISE``, ``FALL``, or ``BOTH`` — which edge(s) the rate applies to.
            rate: Slew rate in amperes per microsecond (A/μs).
            channel: Target channel (1-indexed).
        """

    @abc.abstractmethod
    def output_enable(self, enable: bool, channel: int) -> None:
        """Enable or disable load draw on ``channel``."""

    @abc.abstractmethod
    def get_current(self, channel: int) -> float:
        """Measure the current the load is sinking on ``channel`` (amperes)."""

    @abc.abstractmethod
    def get_voltage(self, channel: int) -> float:
        """Measure the voltage at ``channel``'s terminals (volts)."""


class InstroELoad(Instrument):
    """Electronic-load instrument. Methods return Measurement/Command for publishing."""

    def __init__(
        self,
        name: str,
        driver: ELoadDriverBase,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """Initialize an InstroELoad.

        Args:
            name: Channel-name prefix for published data.
            driver: Concrete E-Load driver; owns its own transport::

                eload = InstroELoad(
                    "main",
                    driver=BK85XXB("ASRL19::INSTR"),
                )

            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher
                (uses the on-disk 'default' Nominal credential).
        """
        super().__init__(name, publishers=publishers, **kwargs)

        self._driver = driver
        self._define_background_daemon()
        self._resource_lock = threading.Lock()

        self._mode: LoadMode | None = None

    @publish_measurement
    def _execute_measurement(
        self,
        driver_method: Callable,
        channel: int = 1,
        channel_suffix: str = "",
        legacy_suffix: str = "",
        **kwargs,
    ) -> Measurement | None:
        """Run ``driver_method(channel=...)`` and package the result as a Measurement."""
        with self._resource_lock:
            val = driver_method(channel=channel)
            timestamp = time.time_ns()

        descriptor = f"ch{channel}_{legacy_suffix}" if self.legacy_naming else f"ch{channel}.{channel_suffix}"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    def open(self):
        """Open the underlying driver."""
        logger.info("Opening E-Load '%s'", self.name)
        self._driver.open()
        logger.info("Opened E-Load '%s'", self.name)

    def close(self):
        """Close the underlying driver and stop the daemon."""
        logger.info("Closing E-Load '%s'", self.name)
        super().close()
        self._driver.close()
        logger.info("Closed E-Load '%s'", self.name)

    @publish_command
    def set_mode(self, mode: LoadMode, channel: int = 1, **kwargs) -> Command:
        """Set the channel's operation mode: CC, CR, CP, or CV. Not all models support every mode."""
        self._mode = mode
        logger.debug("Sending E-Load set_mode command to '%s' on channel %s", self.name, channel)
        with self._resource_lock:
            self._driver.set_mode(mode=mode, channel=channel)
            timestamp = time.time_ns()

        descriptor = f"ch{channel}_mode.cmd" if self.legacy_naming else f"ch{channel}.mode.cmd"
        return self._package_command(descriptor, mode.value, timestamp, **kwargs)

    @publish_command
    def set_level(self, value: float, channel: int = 1, curr_limit: float | None = None, **kwargs) -> Command:
        """Set the operating level in the active mode's units (CC: A, CV: V, CP: W, CR: Ω).

        ``curr_limit`` applies only in CV mode. Raises ``ValueError`` if no mode has been set.
        """
        if self._mode is None:
            raise ValueError("Mode must be set before setting level")

        logger.debug("Sending E-Load set_level command to '%s' on channel %s", self.name, channel)
        with self._resource_lock:
            self._driver.set_level(mode=self._mode, value=value, channel=channel, curr_limit=curr_limit)
            timestamp = time.time_ns()

        # TODO: may need to publish multiple commands due to mode, level value and curr_limit.
        # or we break this up into multiple methods each with a single command return.
        descriptor = f"ch{channel}_level.cmd" if self.legacy_naming else f"ch{channel}.level.cmd"
        return self._package_command(descriptor, value, timestamp, **kwargs)

    @publish_command
    def short_output(self, enable: bool, channel: int = 1, **kwargs) -> Command:
        """Enable or disable the channel short.

        Warning:
            Takes effect IMMEDIATELY. ``enable=True`` shorts the channel output.
        """
        logger.debug("Sending E-Load short_output command to '%s' on channel %s", self.name, channel)
        with self._resource_lock:
            self._driver.short_output(channel=channel, enable=enable)
            timestamp = time.time_ns()

        descriptor = f"ch{channel}_short.cmd" if self.legacy_naming else f"ch{channel}.short.cmd"
        return self._package_command(descriptor, enable, timestamp, **kwargs)

    @publish_command
    def set_range(self, value: float, channel: int = 1, **kwargs) -> Command:
        """Set the range from the expected maximum operating value in the active mode's units (CC: A, CV: V, CP: W, CR: Ω).

        Raises ``ValueError`` if no mode has been set.
        """
        if self._mode is None:
            raise ValueError("Mode must be set before setting range")

        logger.debug("Sending E-Load set_range command to '%s' on channel %s", self.name, channel)
        with self._resource_lock:
            self._driver.set_range(mode=self._mode, value=value, channel=channel)
            timestamp = time.time_ns()

        descriptor = f"ch{channel}_range.cmd" if self.legacy_naming else f"ch{channel}.range.cmd"
        return self._package_command(descriptor, value, timestamp, **kwargs)

    @publish_command
    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int = 1, **kwargs) -> Command:
        """Set the per-edge current slew rate (A/μs) for ``direction`` (RISE/FALL/BOTH)."""
        logger.debug("Sending E-Load set_slewrate command to '%s' on channel %s", self.name, channel)
        with self._resource_lock:
            self._driver.set_slewrate(direction=direction, rate=rate, channel=channel)
            timestamp = time.time_ns()

        descriptor = f"ch{channel}_slewrate.cmd" if self.legacy_naming else f"ch{channel}.slewrate.cmd"
        return self._package_command(descriptor, rate, timestamp, **kwargs)

    @publish_command
    def output_enable(self, enable: bool, channel: int = 1, **kwargs) -> Command:
        """Enable or disable the output on ``channel``."""
        logger.debug("Sending E-Load output_enable command to '%s' on channel %s", self.name, channel)
        with self._resource_lock:
            self._driver.output_enable(enable=enable, channel=channel)
            timestamp = time.time_ns()

        # Legacy ELoad called this suffix `_enable` (vs PSU's `_en`); keep that asymmetry under legacy_naming.
        descriptor = f"ch{channel}_enable.cmd" if self.legacy_naming else f"ch{channel}.enabled.cmd"
        return self._package_command(descriptor, enable, timestamp, **kwargs)

    def get_current(self, channel: int = 1, **kwargs) -> Measurement | None:
        """Measure the current (amperes) sensed on ``channel``. Returns ``None`` if unavailable."""
        return self._execute_measurement(
            self._driver.get_current, channel=channel, channel_suffix="current", legacy_suffix="i", **kwargs
        )

    def get_voltage(self, channel: int = 1, **kwargs) -> Measurement | None:
        """Measure the voltage (volts) sensed on ``channel``. Returns ``None`` if unavailable."""
        return self._execute_measurement(
            self._driver.get_voltage, channel=channel, channel_suffix="voltage", legacy_suffix="v", **kwargs
        )

    def _define_background_daemon(self):
        """Define the background daemon functions for background data collection."""
        self.add_background_daemon_function(self.get_voltage, channel=1)
        self.add_background_daemon_function(self.get_current, channel=1)
