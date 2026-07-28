"""Flow-controller instrument interface and driver contract."""

from __future__ import annotations

import abc
import logging
import threading
import time

from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.unstable.flowcontroller.types import (
    MASS_FLOW_KEY,
    SETPOINT_KEY,
    VOLUMETRIC_FLOW_KEY,
    FlowData,
)

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
    def select_working_fluid(self, fluid_name: str) -> str:
        """Select the active working fluid by name; driver resolves the device-internal identifier."""

    def tare_flow(self) -> FlowData:
        """Zero the flow reading. Device must have zero flow when called. Raises NotImplementedError if controller does not support taring."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support taring")

    @property
    @abc.abstractmethod
    def setpoint(self) -> float:
        """Current setpoint in the device's configured engineering units. Required for all controller types."""

    @property
    def mass_flow(self) -> float:
        """Current mass flow reading. Raises NotImplementedError if controller does not measure mass flow."""
        raise NotImplementedError(f"{self.__class__.__name__} does not measure mass flow")

    @property
    def volumetric_flow(self) -> float:
        """Current volumetric flow reading. Raises NotImplementedError if controller does not measure volumetric flow."""
        raise NotImplementedError(f"{self.__class__.__name__} does not measure volumetric flow")

    @property
    def pressure(self) -> float:
        """Current pressure reading. Raises NotImplementedError if controller does not measure pressure."""
        raise NotImplementedError(f"{self.__class__.__name__} does not measure pressure")

    @property
    @abc.abstractmethod
    def process_value(self) -> float:
        """Current process value (primary feedback measurement for control). Each controller variant returns its primary measured value."""

    @property
    @abc.abstractmethod
    def process_value_source(self) -> str:
        """Key constant (e.g. MASS_FLOW_KEY, VOLUMETRIC_FLOW_KEY, PRESSURE_KEY) indicating which measurement is the process value."""


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
                    driver=AlicatMC("ASRL7::INSTR", "M"),
                )

            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher.

        Note:
            Direct access to driver-specific methods not in FlowControllerDriverBase
            (e.g., AlicatMC.set_loop_control_variable, gas-mixture methods) bypasses
            _resource_lock and is the caller's responsibility to synchronize if mixed
            with concurrent InstroFlowController method calls.
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
                f"{self.name}.{key}": [float(value)] for key, value in data.items() if isinstance(value, (int, float))
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
    def select_working_fluid(self, fluid_name: str, **kwargs) -> Command:
        """Select the active working fluid by name."""
        logger.debug("Sending FlowController select_working_fluid to '%s'", self.name)
        with self._resource_lock:
            fluid_ret = self._driver.select_working_fluid(fluid_name)
            timestamp = time.time_ns()

        return self._package_command("fluid.cmd", fluid_ret, timestamp, **kwargs)

    @publish_command
    def tare_flow(self, **kwargs) -> Command:
        """Zero the flow reading. Device must have zero flow when called."""
        logger.debug("Sending FlowController tare_flow to '%s'", self.name)
        with self._resource_lock:
            self._driver.tare_flow()
            timestamp = time.time_ns()

        return self._package_command("tare.cmd", True, timestamp, **kwargs)

    @publish_measurement
    def get_setpoint(self, **kwargs) -> Measurement | None:
        """Read the current setpoint. A subset of get_flow_data(); driver implementations may fetch a full frame internally."""
        with self._resource_lock:
            value = self._driver.setpoint
            timestamp = time.time_ns()

        return Measurement(
            channel_data={f"{self.name}.{SETPOINT_KEY}": [value]},
            timestamps=[timestamp],
            tags={**self.default_tags, **kwargs},
        )

    @publish_measurement
    def get_mass_flow(self, **kwargs) -> Measurement | None:
        """Read the current mass flow. A subset of get_flow_data(); driver implementations may fetch a full frame internally."""
        with self._resource_lock:
            value = self._driver.mass_flow
            timestamp = time.time_ns()

        return Measurement(
            channel_data={f"{self.name}.{MASS_FLOW_KEY}": [value]},
            timestamps=[timestamp],
            tags={**self.default_tags, **kwargs},
        )

    @publish_measurement
    def get_volumetric_flow(self, **kwargs) -> Measurement | None:
        """Read the current volumetric flow. A subset of get_flow_data(); driver implementations may fetch a full frame internally."""
        with self._resource_lock:
            value = self._driver.volumetric_flow
            timestamp = time.time_ns()

        return Measurement(
            channel_data={f"{self.name}.{VOLUMETRIC_FLOW_KEY}": [value]},
            timestamps=[timestamp],
            tags={**self.default_tags, **kwargs},
        )

    @publish_measurement
    def get_process_value(self, **kwargs) -> Measurement | None:
        """Read the current process value (primary feedback measurement for control).

        The key used in the channel name is determined by the driver's process_value_source property.
        For mass-flow controllers this is mass_flow; for liquid-flow controllers, volumetric_flow; etc.
        """
        with self._resource_lock:
            value = self._driver.process_value
            key = self._driver.process_value_source
            timestamp = time.time_ns()

        return Measurement(
            channel_data={f"{self.name}.{key}": [value]},
            timestamps=[timestamp],
            tags={**self.default_tags, **kwargs},
        )

    def _define_background_daemon(self) -> None:
        """Register background polling functions."""
        self.add_background_daemon_function(self.get_flow_data)
