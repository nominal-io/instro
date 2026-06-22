"""Flow-controller instrument interface and driver contract."""

from __future__ import annotations

import abc
import logging
import threading
import time

from instro.flowcontroller.types import FlowData
from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher

logger = logging.getLogger(__name__)


class FlowControllerDriverBase(abc.ABC):
    """Vendor flow-controller driver contract. Concrete drivers own their transport and lifecycle."""

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport. Idempotent."""

    @abc.abstractmethod
    def get_flow_data(self) -> FlowData:
        """Read a full measurement frame from the device."""

    @abc.abstractmethod
    def set_setpoint(self, setpt: float) -> float:
        """Command a new flow setpoint in the device's configured engineering units."""

    @abc.abstractmethod
    def select_gas(self, gas_name: str) -> str:
        """Select the active gas by name; driver resolves the device-internal number."""

    @abc.abstractmethod
    def tare_flow(self) -> FlowData:
        """Zero the flow reading. Device must have zero flow when called."""


class InstroFlowController(Instrument):
    """Flow-controller instrument. Methods return Measurement/Command for publishing."""

    def __init__(
        self,
        name: str,
        driver: FlowControllerDriverBase,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """Initialize an InstroFlowController.

        Args:
            name: Channel-name prefix for published data.
            driver: Concrete flow-controller driver; owns its own transport::

                fc = InstroFlowController(
                    "main",
                    driver=AlicatMC("ASRL7::INSTR"),
                )

            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher.
        """
        super().__init__(name, publishers=publishers, **kwargs)
        self._driver = driver
        self._resource_lock = threading.Lock()
        self._define_background_daemon()

    def open(self) -> None:
        """Open the underlying driver."""
        logger.info("Opening FlowController '%s'", self.name)
        self._driver.open()
        logger.info("Opened FlowController '%s'", self.name)

    def close(self) -> None:
        """Close the underlying driver and stop the daemon."""
        logger.info("Closing FlowController '%s'", self.name)
        super().close()
        self._driver.close()
        logger.info("Closed FlowController '%s'", self.name)

    @publish_measurement
    def get_flow_data(self, **kwargs) -> Measurement | None:
        """Poll the device and publish all live measurements at once."""
        with self._resource_lock:
            data = self._driver.get_flow_data()
            timestamp = time.time_ns()

        return Measurement(
            channel_data={
                f"{self.name}.setpoint": [data.setpoint],
                f"{self.name}.mass_flow": [data.mass_flow],
                f"{self.name}.vol_flow": [data.vol_flow],
                f"{self.name}.pressure": [data.pressure],
                f"{self.name}.temperature": [data.temperature],
            },
            timestamps=[timestamp],
            tags={**self.default_tags, **kwargs},
        )

    @publish_command
    def set_setpoint(self, value: float, **kwargs) -> Command:
        """Command a new flow setpoint in the device's configured engineering units."""
        logger.debug("Sending FlowController set_setpoint to '%s'", self.name)
        with self._resource_lock:
            setpoint = self._driver.set_setpoint(value)
            timestamp = time.time_ns()

        return self._package_command("setpoint.cmd", setpoint, timestamp, **kwargs)

    @publish_command
    def select_gas(self, gas_name: str, **kwargs) -> Command:
        """Select the active gas by name."""
        logger.debug("Sending FlowController select_gas to '%s'", self.name)
        with self._resource_lock:
            gas_ret = self._driver.select_gas(gas_name)
            timestamp = time.time_ns()

        return self._package_command("gas.cmd", gas_ret, timestamp, **kwargs)

    @publish_command
    def tare_flow(self, **kwargs) -> Command:
        """Zero the flow reading. Device must have zero flow when called."""
        logger.debug("Sending FlowController tare_flow to '%s'", self.name)
        with self._resource_lock:
            self._driver.tare_flow()
            timestamp = time.time_ns()

        return self._package_command("tare.cmd", True, timestamp, **kwargs)

    def _define_background_daemon(self) -> None:
        """Register background polling functions."""
        self.add_background_daemon_function(self.get_flow_data)
