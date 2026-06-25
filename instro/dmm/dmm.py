"""Digital multimeter (DMM) instrument interface and driver contract."""

from __future__ import annotations

import abc
import logging
import threading
import time
from dataclasses import replace
from typing import Callable

from instro.dmm.types import DMMMeasurementConfig, MeasurementFunction, RangeMode
from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher

logger = logging.getLogger(__name__)


class DMMDriverBase(abc.ABC):
    """Vendor DMM driver contract. Concrete drivers own their transport and lifecycle.

    Range and NPLC are split per function (``set_dc_voltage_range``,
    ``set_ac_current_nplc``, …) so the driver never tracks the active
    function — ``InstroDMM`` dispatches based on its own ``_measurement_config``.
    """

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport and put the instrument into a known state.

        Concrete drivers also do one-shot setup here (e.g. ``*CLS`` to clear
        the error queue, ``SYST:REM`` for remote control).
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport. Idempotent."""

    @abc.abstractmethod
    def set_measurement_function(self, function: MeasurementFunction) -> None:
        """Configure the DMM for ``function`` (DC voltage, AC current, 2-wire resistance, …).

        Drivers may issue a dummy measurement to commit the mode change.
        ``MeasurementFunction`` values the instrument doesn't support should
        raise ``NotImplementedError``.
        """

    def set_digits(self, n: int) -> None:
        """Set resolution to ``n`` digits.

        Default raises ``NotImplementedError``; override on drivers whose
        SCPI exposes a direct digits/resolution control. Instruments that
        only expose integration time (e.g. Keithley 2400) should leave this
        as a no-implementation and direct users to ``set_aperture_nplc``.
        """
        raise NotImplementedError("set_digits is not supported by this driver")

    def set_aperture_seconds(self, seconds: float) -> None:
        """Set integration time directly in seconds.

        Default raises ``NotImplementedError``. Mutually exclusive with
        ``set_aperture_nplc`` on a given driver — vendors typically expose
        one or the other.
        """
        raise NotImplementedError("set_aperture_seconds is not supported by this driver")

    # --- Range, scoped per measurement function. Override per function. ---
    #
    # Range and NPLC are split per function so the driver never has to track
    # which measurement mode is active. InstroDMM dispatches to the right
    # method based on its tracked _measurement_config.function, which keeps
    # the measurement function as a single source of truth on InstroDMM. Each
    # method takes ``value: float | None``; ``None`` means auto-range.

    def set_dc_voltage_range(self, value: float | None) -> None:
        """Set manual DC-voltage range to ``value`` volts; ``None`` selects auto-range."""
        raise NotImplementedError("set_dc_voltage_range is not supported by this driver")

    def set_ac_voltage_range(self, value: float | None) -> None:
        """Set manual AC-voltage range to ``value`` volts; ``None`` selects auto-range."""
        raise NotImplementedError("set_ac_voltage_range is not supported by this driver")

    def set_dc_current_range(self, value: float | None) -> None:
        """Set manual DC-current range to ``value`` amperes; ``None`` selects auto-range."""
        raise NotImplementedError("set_dc_current_range is not supported by this driver")

    def set_ac_current_range(self, value: float | None) -> None:
        """Set manual AC-current range to ``value`` amperes; ``None`` selects auto-range."""
        raise NotImplementedError("set_ac_current_range is not supported by this driver")

    def set_two_wire_resistance_range(self, value: float | None) -> None:
        """Set manual 2-wire-resistance range to ``value`` ohms; ``None`` selects auto-range."""
        raise NotImplementedError("set_two_wire_resistance_range is not supported by this driver")

    def set_four_wire_resistance_range(self, value: float | None) -> None:
        """Set manual 4-wire-resistance range to ``value`` ohms; ``None`` selects auto-range."""
        raise NotImplementedError("set_four_wire_resistance_range is not supported by this driver")

    # --- Aperture (NPLC), scoped per measurement function. Override per function. ---
    #
    # NPLC = number of power-line cycles. Higher NPLC integrates over more AC
    # mains noise (better DC accuracy) at the cost of measurement rate.

    def set_dc_voltage_nplc(self, nplc: float) -> None:
        """Set DC-voltage integration time to ``nplc`` power-line cycles."""
        raise NotImplementedError("set_dc_voltage_nplc is not supported by this driver")

    def set_ac_voltage_nplc(self, nplc: float) -> None:
        """Set AC-voltage integration time to ``nplc`` power-line cycles."""
        raise NotImplementedError("set_ac_voltage_nplc is not supported by this driver")

    def set_dc_current_nplc(self, nplc: float) -> None:
        """Set DC-current integration time to ``nplc`` power-line cycles."""
        raise NotImplementedError("set_dc_current_nplc is not supported by this driver")

    def set_ac_current_nplc(self, nplc: float) -> None:
        """Set AC-current integration time to ``nplc`` power-line cycles."""
        raise NotImplementedError("set_ac_current_nplc is not supported by this driver")

    def set_two_wire_resistance_nplc(self, nplc: float) -> None:
        """Set 2-wire-resistance integration time to ``nplc`` power-line cycles."""
        raise NotImplementedError("set_two_wire_resistance_nplc is not supported by this driver")

    def set_four_wire_resistance_nplc(self, nplc: float) -> None:
        """Set 4-wire-resistance integration time to ``nplc`` power-line cycles."""
        raise NotImplementedError("set_four_wire_resistance_nplc is not supported by this driver")

    # --- Measurements ---

    def measure_four_wire_resistance(self) -> float:
        """Trigger a 4-wire-resistance measurement and return the value (ohms).

        Default raises ``NotImplementedError``; override on drivers whose
        instrument has dedicated sense leads (e.g. Agilent 34401A).
        """
        raise NotImplementedError("4-wire resistance not supported by this driver")

    @abc.abstractmethod
    def measure_dc_voltage(self) -> float:
        """Trigger a DC-voltage measurement and return the value (volts)."""

    @abc.abstractmethod
    def measure_ac_voltage(self) -> float:
        """Trigger an AC-voltage measurement and return the value (volts RMS, vendor-dependent)."""

    @abc.abstractmethod
    def measure_resistance(self) -> float:
        """Trigger a 2-wire-resistance measurement and return the value (ohms)."""

    @abc.abstractmethod
    def measure_dc_current(self) -> float:
        """Trigger a DC-current measurement and return the value (amperes)."""

    @abc.abstractmethod
    def measure_ac_current(self) -> float:
        """Trigger an AC-current measurement and return the value (amperes RMS, vendor-dependent)."""


class InstroDMM(Instrument):
    """Digital multimeter instrument. Call ``set_measurement_function`` then ``read``."""

    def __init__(
        self,
        name: str,
        driver: DMMDriverBase,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """Initialize an InstroDMM.

        Args:
            name: Channel-name prefix for published data.
            driver: Concrete DMM driver; owns its own transport::

                dmm = InstroDMM(
                    "main",
                    driver=Agilent34401A("ASRL3::INSTR"),
                )

            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher
                (uses the on-disk 'default' Nominal credential).
        """
        super().__init__(name, publishers=publishers, **kwargs)

        self._driver = driver
        self._resource_lock = threading.Lock()
        self._measurement_config: DMMMeasurementConfig | None = None

        self._define_background_daemon()

    def start(self) -> None:
        """Start the background daemon thread.

        Raises:
            ValueError: ``set_measurement_function`` has not been called.
        """
        if self._measurement_config is None:
            raise ValueError("set_measurement_function must be called before starting background collection")

        super().start()

    def open(self) -> None:
        """Open the underlying driver."""
        logger.info("Opening DMM '%s'", self.name)
        self._driver.open()
        logger.info("Opened DMM '%s'", self.name)

    def close(self) -> None:
        """Close the underlying driver and stop the daemon."""
        logger.info("Closing DMM '%s'", self.name)
        super().close()
        self._driver.close()
        logger.info("Closed DMM '%s'", self.name)

    @publish_command
    def set_measurement_function(self, function: MeasurementFunction, **kwargs) -> Command:
        """Configure the DMM for ``function`` (DC voltage, resistance, etc.)."""
        logger.debug("Sending DMM set_measurement_function command to '%s'", self.name)
        with self._resource_lock:
            self._driver.set_measurement_function(function)
            timestamp = time.time_ns()

        if self._measurement_config is None:
            self._measurement_config = DMMMeasurementConfig(function)
        else:
            self._measurement_config = replace(self._measurement_config, function=function)

        return self._package_command("set_measurement_function.cmd", function.value, timestamp, **kwargs)

    @publish_command
    def set_digits(self, n: int, **kwargs) -> Command:
        """Set resolution in digits. Requires set_measurement_function() to have been called."""
        if self._measurement_config is None:
            raise ValueError("set_measurement_function must be called before set_digits")
        logger.debug("Sending DMM set_digits command to '%s'", self.name)
        with self._resource_lock:
            self._driver.set_digits(n)
            timestamp = time.time_ns()
        self._measurement_config = replace(self._measurement_config, digits=n)
        return self._package_command("digits.cmd", n, timestamp, **kwargs)

    @publish_command
    def set_aperture_seconds(self, seconds: float, **kwargs) -> Command:
        """Set integration time (aperture) in seconds. Requires set_measurement_function() first."""
        if self._measurement_config is None:
            raise ValueError("set_measurement_function must be called before setting the aperture")

        logger.debug("Sending DMM set_aperture_seconds command to '%s'", self.name)
        with self._resource_lock:
            self._driver.set_aperture_seconds(seconds)
            timestamp = time.time_ns()
        self._measurement_config = replace(self._measurement_config, aperture_seconds=seconds, aperture_nplc=None)
        return self._package_command("aperture_seconds.cmd", seconds, timestamp, **kwargs)

    @publish_command
    def set_aperture_nplc(self, nplc: float, **kwargs) -> Command:
        """Set integration time as a number of power-line cycles.

        Higher NPLC integrates over more AC noise (better DC/DCI/resistance
        accuracy) at the cost of measurement rate. NPLC=100 is the typical
        highest-accuracy setting. Requires ``set_measurement_function`` first.
        """
        if self._measurement_config is None:
            raise ValueError("set_measurement_function must be called before setting the aperture")

        logger.debug("Sending DMM set_aperture_nplc command to '%s'", self.name)
        with self._resource_lock:
            self._get_driver_set_nplc_method(self._measurement_config.function)(nplc)
            timestamp = time.time_ns()
        self._measurement_config = replace(self._measurement_config, aperture_seconds=None, aperture_nplc=nplc)
        return self._package_command("aperture_nplc.cmd", nplc, timestamp, **kwargs)

    @publish_command
    def set_range(self, value: float | None, **kwargs) -> Command:
        """Set manual range; ``None`` selects auto-range. Requires ``set_measurement_function`` first.

        Publishes ``range_mode.cmd`` (``"AUTO"``/``"MANUAL"``) on every call and
        ``range.cmd`` (float) only when a manual range is supplied — splitting them
        keeps each channel single-typed, which the Nominal streaming backend requires.
        """
        if self._measurement_config is None:
            raise ValueError("set_measurement_function must be called before set_range")
        logger.debug("Sending DMM set_range command to '%s'", self.name)
        with self._resource_lock:
            self._get_driver_set_range_method(self._measurement_config.function)(value)
            timestamp = time.time_ns()
        self._measurement_config = replace(self._measurement_config, range=value)
        range_mode = RangeMode.MANUAL if value is not None else RangeMode.AUTO
        channel_data: dict[str, float | str] = {
            f"{self.name}.range_mode.cmd": range_mode.value,
        }
        if value is not None:
            channel_data[f"{self.name}.range.cmd"] = float(value)
        return Command(
            channel_data=channel_data,
            timestamp=timestamp,
            tags={**self.default_tags, **(kwargs or {})},
        )

    @publish_measurement
    def read(self, **kwargs) -> Measurement:
        """Trigger a measurement under the configured function and return it. Requires ``set_measurement_function`` first."""
        if self._measurement_config is None:
            raise ValueError("set_measurement_function must be called before read")

        with self._resource_lock:
            read_method = self._get_driver_read_method(self._measurement_config.function)
            response = read_method()
            timestamp = time.time_ns()

        channel_suffix = self._measurement_config.function.value.lower()
        return self._package_measurement(channel_suffix, response, timestamp, **kwargs)

    def _get_driver_read_method(self, function: MeasurementFunction) -> Callable:
        return {
            MeasurementFunction.DC_VOLTAGE: self._driver.measure_dc_voltage,
            MeasurementFunction.AC_VOLTAGE: self._driver.measure_ac_voltage,
            MeasurementFunction.DC_CURRENT: self._driver.measure_dc_current,
            MeasurementFunction.AC_CURRENT: self._driver.measure_ac_current,
            MeasurementFunction.TWO_WIRE_RESISTANCE: self._driver.measure_resistance,
            MeasurementFunction.FOUR_WIRE_RESISTANCE: self._driver.measure_four_wire_resistance,
        }[function]

    def _get_driver_set_range_method(self, function: MeasurementFunction) -> Callable:
        return {
            MeasurementFunction.DC_VOLTAGE: self._driver.set_dc_voltage_range,
            MeasurementFunction.AC_VOLTAGE: self._driver.set_ac_voltage_range,
            MeasurementFunction.DC_CURRENT: self._driver.set_dc_current_range,
            MeasurementFunction.AC_CURRENT: self._driver.set_ac_current_range,
            MeasurementFunction.TWO_WIRE_RESISTANCE: self._driver.set_two_wire_resistance_range,
            MeasurementFunction.FOUR_WIRE_RESISTANCE: self._driver.set_four_wire_resistance_range,
        }[function]

    def _get_driver_set_nplc_method(self, function: MeasurementFunction) -> Callable:
        return {
            MeasurementFunction.DC_VOLTAGE: self._driver.set_dc_voltage_nplc,
            MeasurementFunction.AC_VOLTAGE: self._driver.set_ac_voltage_nplc,
            MeasurementFunction.DC_CURRENT: self._driver.set_dc_current_nplc,
            MeasurementFunction.AC_CURRENT: self._driver.set_ac_current_nplc,
            MeasurementFunction.TWO_WIRE_RESISTANCE: self._driver.set_two_wire_resistance_nplc,
            MeasurementFunction.FOUR_WIRE_RESISTANCE: self._driver.set_four_wire_resistance_nplc,
        }[function]

    def _define_background_daemon(self):
        """Register the default background-daemon function (continuous read())."""
        self.add_background_daemon_function(self.read)
