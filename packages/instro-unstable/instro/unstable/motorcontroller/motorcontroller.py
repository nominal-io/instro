"""Motor-controller instrument interface and driver contract."""

from __future__ import annotations

import abc
import logging
import threading
import time

from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.unstable.motorcontroller.types import MotorTelemetry

logger = logging.getLogger(__name__)


class MotorControllerDriverBase(abc.ABC):
    """Vendor motor-controller driver contract. Concrete drivers own their transport and lifecycle."""

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport."""

    @abc.abstractmethod
    def close(self) -> None:
        """Safe-stop the motor and close the underlying transport. Idempotent."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Cease motion by the safest means the device supports; holding position afterwards is not guaranteed."""

    @abc.abstractmethod
    def get_telemetry(self) -> MotorTelemetry:
        """Latest telemetry snapshot; only fields the device reports are present."""

    def enable(self) -> None:
        """Energize the power stage into closed-loop control. Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support explicit enable")

    def disable(self) -> None:
        """De-energize the power stage; the motor coasts freely. Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support explicit disable")

    def set_duty_cycle(self, duty: float) -> None:
        """Command a voltage fraction in -1.0..1.0. Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support duty-cycle control")

    def set_current(self, amps: float) -> None:
        """Command a signed motor current in amps. Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support current control")

    def set_brake_current(self, amps: float) -> None:
        """Command a dissipative braking current in amps (>= 0). Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support brake-current control")

    def set_velocity(self, rpm: float) -> None:
        """Command a signed mechanical speed in RPM. Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support velocity control")

    def set_position(self, degrees: float) -> None:
        """Command a target position in degrees. Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support position control")


class InstroMotorController(Instrument):
    """Motor-controller instrument. Methods return Measurement/Command for publishing."""

    def __init__(
        self,
        name: str,
        driver: MotorControllerDriverBase,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """Initialize an InstroMotorController.

        Args:
            name: Channel-name prefix for published data.
            driver: Concrete motor-controller driver; owns its own transport::

                motor = InstroMotorController(
                    "drive",
                    driver=VESC6(channel=0, pole_pairs=7),
                )

            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher.

        Note:
            Drivers are not thread-safe on their own; InstroMotorController serializes
            all driver access through _resource_lock (including the background
            telemetry daemon). Calling driver methods directly while the instrument
            is in use bypasses that lock.
        """
        super().__init__(name, publishers=publishers, **kwargs)
        self._driver = driver
        self._resource_lock = threading.Lock()
        self._define_background_daemon()

    def open(self) -> None:
        """Open the underlying driver."""
        logger.info("Opening MotorController '%s'", self.name)
        self._driver.open()
        logger.info("Opened MotorController '%s'", self.name)

    def close(self) -> None:
        """Close the underlying driver and stop the daemon."""
        logger.info("Closing MotorController '%s'", self.name)
        super().close()
        self._driver.close()
        logger.info("Closed MotorController '%s'", self.name)

    @publish_command
    def enable_motor(self, **kwargs) -> Command:
        """Energize the power stage into closed-loop control."""
        logger.debug("Sending MotorController enable_motor to '%s'", self.name)
        with self._resource_lock:
            self._driver.enable()
            timestamp = time.time_ns()

        return self._package_command("enable.cmd", True, timestamp, **kwargs)

    @publish_command
    def disable_motor(self, **kwargs) -> Command:
        """De-energize the power stage; the motor coasts freely."""
        logger.debug("Sending MotorController disable_motor to '%s'", self.name)
        with self._resource_lock:
            self._driver.disable()
            timestamp = time.time_ns()

        return self._package_command("enable.cmd", False, timestamp, **kwargs)

    @publish_command
    def stop_motor(self, **kwargs) -> Command:
        """Cease motion by the safest means the device supports. Named stop_motor because Instrument.stop() stops the background daemon."""
        logger.debug("Sending MotorController stop_motor to '%s'", self.name)
        with self._resource_lock:
            self._driver.stop()
            timestamp = time.time_ns()

        return self._package_command("stop.cmd", True, timestamp, **kwargs)

    @publish_command
    def set_duty_cycle(self, duty: float, **kwargs) -> Command:
        """Command a voltage fraction in -1.0..1.0."""
        logger.debug("Sending MotorController set_duty_cycle to '%s'", self.name)
        with self._resource_lock:
            self._driver.set_duty_cycle(duty)
            timestamp = time.time_ns()

        return self._package_command("duty_cycle.cmd", duty, timestamp, **kwargs)

    @publish_command
    def set_current(self, amps: float, **kwargs) -> Command:
        """Command a signed motor current in amps."""
        logger.debug("Sending MotorController set_current to '%s'", self.name)
        with self._resource_lock:
            self._driver.set_current(amps)
            timestamp = time.time_ns()

        return self._package_command("current.cmd", amps, timestamp, **kwargs)

    @publish_command
    def set_brake_current(self, amps: float, **kwargs) -> Command:
        """Command a dissipative braking current in amps (>= 0)."""
        logger.debug("Sending MotorController set_brake_current to '%s'", self.name)
        with self._resource_lock:
            self._driver.set_brake_current(amps)
            timestamp = time.time_ns()

        return self._package_command("brake_current.cmd", amps, timestamp, **kwargs)

    @publish_command
    def set_velocity(self, rpm: float, **kwargs) -> Command:
        """Command a signed mechanical speed in RPM."""
        logger.debug("Sending MotorController set_velocity to '%s'", self.name)
        with self._resource_lock:
            self._driver.set_velocity(rpm)
            timestamp = time.time_ns()

        return self._package_command("velocity.cmd", rpm, timestamp, **kwargs)

    @publish_command
    def set_position(self, degrees: float, **kwargs) -> Command:
        """Command a target position in degrees."""
        logger.debug("Sending MotorController set_position to '%s'", self.name)
        with self._resource_lock:
            self._driver.set_position(degrees)
            timestamp = time.time_ns()

        return self._package_command("position.cmd", degrees, timestamp, **kwargs)

    @publish_measurement
    def get_telemetry(self, **kwargs) -> Measurement | None:
        """Poll the driver and publish all telemetry fields it reports; None when the device reported nothing."""
        with self._resource_lock:
            data = self._driver.get_telemetry()
            timestamp = time.time_ns()

        if not data:
            return None

        return Measurement(
            channel_data={f"{self.name}.{key}": [float(value)] for key, value in data.items()},
            timestamps=[timestamp],
            tags={**self.default_tags, **kwargs},
        )

    def _define_background_daemon(self) -> None:
        """Register background polling functions."""
        self.add_background_daemon_function(self.get_telemetry)
