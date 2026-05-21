"""Power supply (PSU) instrument interface and driver contract."""

from __future__ import annotations

import abc
import logging
import threading
import time
from typing import Any, Callable

from instro.utils import Command, Instrument, Measurement
from instro.utils.instrument import publish_command, publish_measurement
from instro.utils.publishers.publisher import Publisher

logger = logging.getLogger(__name__)


class FeatureNotSupportedError(Exception):
    """Raised when a PSU driver is asked to use a feature its hardware does not expose."""


class PSUDriverBase(abc.ABC):
    """Vendor PSU driver contract. Concrete drivers own their transport and lifecycle.

    Every method is ``@abc.abstractmethod``. Drivers whose hardware does not expose
    a given feature raise ``FeatureNotSupportedError`` from their override.
    """

    @abc.abstractmethod
    def open(self) -> None:
        """Open the driver's underlying transport."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the driver's underlying transport."""

    @abc.abstractmethod
    def set_voltage(self, voltage: float, channel: int) -> None:
        """Set the output voltage (volts) on `channel`."""

    @abc.abstractmethod
    def get_voltage(self, channel: int) -> float:
        """Query the measured output voltage (volts) on `channel`."""

    @abc.abstractmethod
    def set_current_limit(self, current_limit: float, channel: int) -> None:
        """Set the current limit (amperes) on `channel`."""

    @abc.abstractmethod
    def get_current(self, channel: int) -> float:
        """Query the measured output current (amperes) on `channel`."""

    @abc.abstractmethod
    def output_enable(self, enable: bool, channel: int) -> None:
        """Enable or disable the output on `channel`."""

    @abc.abstractmethod
    def get_output_status(self, channel: int) -> bool:
        """Query whether the output on `channel` is enabled."""

    @abc.abstractmethod
    def set_overvoltage_protection(self, voltage: float, channel: int) -> None:
        """Set the overvoltage protection threshold (volts) on `channel`."""

    @abc.abstractmethod
    def get_overvoltage_protection(self, channel: int) -> float:
        """Query the overvoltage protection threshold (volts) on `channel`."""

    @abc.abstractmethod
    def set_overcurrent_protection(self, current: float, channel: int) -> None:
        """Set the overcurrent protection threshold (amperes) on `channel`."""

    @abc.abstractmethod
    def get_overcurrent_protection(self, channel: int) -> float:
        """Query the overcurrent protection threshold (amperes) on `channel`."""

    @abc.abstractmethod
    def set_remote_sense(self, enabled: bool, channel: int) -> None:
        """Enable or disable remote sense on `channel`."""

    @abc.abstractmethod
    def get_remote_sense(self, channel: int) -> bool:
        """Query whether remote sense is enabled on `channel`."""


class InstroPSU(Instrument):
    """Power-supply instrument. Methods return Measurement/Command for publishing."""

    def __init__(
        self,
        name: str,
        driver: PSUDriverBase,
        num_channels: int,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """Initialize an InstroPSU.

        Args:
            name: Channel-name prefix for published data.
            driver: Concrete PSU driver; owns its own transport::

                psu = InstroPSU(
                    name="main",
                    driver=BK9115("USB0::0xFFFF::0x9115::SN::INSTR"),
                    num_channels=1,
                )

            num_channels: Number of output channels on this PSU.
            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher
                (uses the on-disk 'default' Nominal credential).
        """
        super().__init__(name, publishers=publishers, **kwargs)

        self._driver = driver
        self._num_channels = num_channels
        self._resource_lock = threading.Lock()

        self._define_background_daemon()

    @publish_command
    def _execute_command(
        self,
        driver_method: Callable,
        value: Any,
        channel: int = 1,
        channel_suffix: str = "",
        legacy_suffix: str = "",
        **kwargs,
    ) -> Command:
        """Execute a driver command method and return a Command for the published value."""
        with self._resource_lock:
            driver_method(value, channel=channel)
            timestamp = time.time_ns()

        if self.legacy_naming:
            descriptor = f"ch{channel}_{legacy_suffix}.cmd"
        else:
            descriptor = f"ch{channel}.{channel_suffix}.cmd"
        return self._package_command(descriptor, value, timestamp, **kwargs)

    @publish_measurement
    def _execute_measurement(
        self,
        driver_method: Callable,
        channel: int = 1,
        channel_suffix: str = "",
        legacy_suffix: str = "",
        **kwargs,
    ) -> Measurement | None:
        """Execute a driver measurement method and return a Measurement for the read value."""
        with self._resource_lock:
            val = driver_method(channel=channel)
            timestamp = time.time_ns()

        if self.legacy_naming:
            descriptor = f"ch{channel}_{legacy_suffix}"
        else:
            descriptor = f"ch{channel}.{channel_suffix}"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    def open(self):
        """Establish connection to the device."""
        logger.info("Opening PSU '%s'", self.name)
        self._driver.open()
        logger.info("Opened PSU '%s'", self.name)

    def close(self):
        """Disconnect from the device."""
        logger.info("Closing PSU '%s'", self.name)
        super().close()
        self._driver.close()
        logger.info("Closed PSU '%s'", self.name)

    def set_voltage(self, voltage: float, channel: int = 1, **kwargs) -> Command:
        """Set the output voltage (volts) on ``channel``."""
        return self._execute_command(
            driver_method=self._driver.set_voltage,
            value=voltage,
            channel=channel,
            channel_suffix="voltage",
            legacy_suffix="v",
            **kwargs,
        )

    def get_voltage(self, channel: int = 1, **kwargs) -> Measurement | None:
        """Measure the voltage (volts) sensed at ``channel`` terminals. Returns ``None`` if unavailable."""
        return self._execute_measurement(
            self._driver.get_voltage, channel=channel, channel_suffix="voltage", legacy_suffix="v", **kwargs
        )

    def set_current_limit(self, current_limit: float, channel: int = 1, **kwargs) -> Command:
        """Set the current limit (amperes) on ``channel``."""
        return self._execute_command(
            self._driver.set_current_limit,
            value=current_limit,
            channel=channel,
            channel_suffix="current",
            legacy_suffix="i",
            **kwargs,
        )

    def get_current(self, channel: int = 1, **kwargs) -> Measurement | None:
        """Measure the current (amperes) flowing through ``channel``. Returns ``None`` if unavailable."""
        return self._execute_measurement(
            self._driver.get_current, channel=channel, channel_suffix="current", legacy_suffix="i", **kwargs
        )

    def output_enable(self, enable: bool, channel: int = 1, **kwargs) -> Command:
        """Enable or disable the output on ``channel``."""
        return self._execute_command(
            self._driver.output_enable,
            value=enable,
            channel=channel,
            channel_suffix="enabled",
            legacy_suffix="en",
            **kwargs,
        )

    def get_output_status(self, channel: int = 1, **kwargs) -> Measurement | None:
        """Query whether the output on ``channel`` is enabled. Returns ``None`` if unavailable."""
        return self._execute_measurement(
            self._driver.get_output_status,
            channel=channel,
            channel_suffix="enabled",
            legacy_suffix="en",
            **kwargs,
        )

    def set_overvoltage_protection(self, voltage: float, channel: int = 1, **kwargs) -> Command:
        """Set the overvoltage protection threshold (volts) on ``channel``."""
        return self._execute_command(
            self._driver.set_overvoltage_protection,
            value=voltage,
            channel=channel,
            channel_suffix="ovp",
            legacy_suffix="ovp",
            **kwargs,
        )

    def get_overvoltage_protection(self, channel: int = 1, **kwargs) -> Measurement | None:
        """Query the overvoltage protection threshold (volts) on ``channel``. Returns ``None`` if unavailable."""
        return self._execute_measurement(
            self._driver.get_overvoltage_protection,
            channel=channel,
            channel_suffix="ovp",
            legacy_suffix="ovp",
            **kwargs,
        )

    def set_overcurrent_protection(self, current: float, channel: int = 1, **kwargs) -> Command:
        """Set the overcurrent protection threshold (amperes) on ``channel``."""
        return self._execute_command(
            self._driver.set_overcurrent_protection,
            value=current,
            channel=channel,
            channel_suffix="ocp",
            legacy_suffix="ocp",
            **kwargs,
        )

    def get_overcurrent_protection(self, channel: int = 1, **kwargs) -> Measurement | None:
        """Query the overcurrent protection threshold (amperes) on ``channel``. Returns ``None`` if unavailable."""
        return self._execute_measurement(
            self._driver.get_overcurrent_protection,
            channel=channel,
            channel_suffix="ocp",
            legacy_suffix="ocp",
            **kwargs,
        )

    def set_remote_sense(self, enabled: bool, channel: int = 1, **kwargs) -> Command:
        """Enable or disable remote sense on ``channel``."""
        return self._execute_command(
            self._driver.set_remote_sense,
            value=enabled,
            channel=channel,
            channel_suffix="remote_sense",
            legacy_suffix="rs",
            **kwargs,
        )

    def get_remote_sense(self, channel: int = 1, **kwargs) -> Measurement | None:
        """Query whether remote sense is enabled on ``channel``. Returns ``None`` if unavailable."""
        return self._execute_measurement(
            self._driver.get_remote_sense,
            channel=channel,
            channel_suffix="remote_sense",
            legacy_suffix="rs",
            **kwargs,
        )

    def _define_background_daemon(self):
        """Register background daemon functions for voltage/current/enable on every channel."""
        for i in range(1, self._num_channels + 1):
            self.add_background_daemon_function(self.get_voltage, channel=i)
            self.add_background_daemon_function(self.get_current, channel=i)
            self.add_background_daemon_function(self.get_output_status, channel=i)
