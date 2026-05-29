"""Registers scanner driver base interface.

Defines `RegisterBase`, `RegisterDriverBase` and `InstroRegisterInstrument`.

Public API:
    RegisterDriverBase
    InstroRegisterInstrument
    RegisterBase

"""

from __future__ import annotations

import abc
import logging
import time
from typing import Sequence

from pydantic import BaseModel, Field

from instro.utils import Command, Instrument, Measurement
from instro.utils.publishers.publisher import Publisher
from instro.utils.types import ScaleType

logger = logging.getLogger(__name__)

timestamp_ns_type = int
register_value_type = int | float


class RegisterBase(BaseModel):
    """Definition of a generic register.

    Most features will need to be implemented in a descendent class.

    Note:
        - ``write_value_map`` is intended to permit enumerations and is a map from
            a string name to a value in engineering units
        - ``scale`` is a standard function to convert between engineering units and
            any underlying data value -- this is distinct from any protocol-specific
            register-level data manipulation
    """

    name: str = Field(description="Unique name/alias for this register")
    description: str | None = None
    poll: bool = True
    scale: ScaleType | None = None
    write_value_map: dict[str, register_value_type] | None = None
    read_group: str | None = None

    def _apply_scaling(self, raw_value: int | float) -> int | float:
        """Apply scaling if defined, otherwise return raw value."""
        if self.scale is not None:
            return self.scale.to_physical(raw_value)
        return raw_value

    def _string_to_value_map(self, s: str) -> int | float:
        if self.write_value_map is None:
            raise KeyError(
                f"Register '{self.name}' has no write_value_map. "
                f"Cannot write string '{s}' — pass a numeric value instead."
            )
        if s not in self.write_value_map:
            raise KeyError(
                f"'{s}' is not a valid value for register '{self.name}'. "
                f"Available values: {list(self.write_value_map.keys())}"
            )
        value = self.write_value_map[s]

        return value


class RegisterDriverBase(abc.ABC):
    """Abstract base class for tag-oriented devices.

    Concrete drivers own their transport setup and translate category-level
    calls into vendor-specific commands. The base declares only the category
    contract; transport choice and lifecycle live in the concrete driver.
    """

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport or protocol."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport or protocol."""

    @abc.abstractmethod
    def read(self, register_id: str) -> register_value_type:
        """Reads a single register value from the underlying device.

        Note that some driver implementations may have a different definition of register.
        Ex: A single value read by this API could be spread across multiple modbus registers.
        """

    @abc.abstractmethod
    def read_raw_scaled(self, register_id: str) -> tuple[register_value_type, register_value_type]:
        """Reads a single register value from the underlying device, returning the raw and scaled value.

        Note that some driver implementations may have a different definition of register.
        Ex: A single value read by this API could be spread across multiple modbus registers.
        """

    @abc.abstractmethod
    def write(self, register_id: str, value: register_value_type | str) -> register_value_type:
        """Write a single register value to the underlying device, returning the written value.

        Note that some driver implementations may have a different definition of register.
        Ex: A single value written by this API could be spread across multiple modbus registers.
        """

    @abc.abstractmethod
    def read_group(self, group_id: str) -> list[register_value_type]:
        """Reads a group of registers from the device, returning scaled values.

        Note that some driver implementations may have a different definition of register.
        Ex: A single value read by this API could be spread across multiple modbus registers.
        """

    @abc.abstractmethod
    def read_group_raw_scaled(self, group_id: str) -> tuple[list[register_value_type], list[register_value_type]]:
        """Reads a group of registers from the device, returning raw and scaled values.

        Note that some driver implementations may have a different definition of register.
        Ex: A single value read by this API could be spread across multiple modbus registers.
        """

    @abc.abstractmethod
    def write_group(self, group_id: str, values: list[register_value_type | str]) -> list[register_value_type]:
        """Writes a set of scaled values to a group of registers.

        The values field supports numeric types as well as enumerated values as defined by each register.

        Note that some driver implementations may have a different definition of register.
        Ex: A single value read by this API could be spread across multiple modbus registers.
        """

    @property
    @abc.abstractmethod
    def device_name(self) -> str:
        """Returns the name of the connected device.

        The driver is responsible for determining the best way to provide this name. In most cases,
        it should be a user-defined alias appropriate to reporting data. If no user-defined alias
        is provided, the driver should return an intelligent identifier (such as a serial number)
        """

    @property
    @abc.abstractmethod
    def writeable_registers(self) -> Sequence[RegisterBase]:
        """Returns a sequence of all registers which may be written by the user.

        This sequence is intended to be immutable, but the underlying storage is a list.
        """

    @property
    @abc.abstractmethod
    def readable_registers(self) -> Sequence[RegisterBase]:
        """Returns a sequence of all registers which may be read by the user.

        This sequence is intended to be immutable, but the underlying storage is a list.
        """

    @property
    @abc.abstractmethod
    def writeable_groups(self) -> list[str]:
        """Returns a list of all register groups which may be written by the user."""

    @property
    @abc.abstractmethod
    def readable_groups(self) -> list[str]:
        """Returns a list of all register groups which may be read by the user."""

    @abc.abstractmethod
    def enumerate_group_registers(self, group_id: str) -> Sequence[str]:
        """Returns the register names associated with a group, in address order."""

    def build_extra_channels(
        self, register_id: str, raw: register_value_type, scaled: register_value_type
    ) -> dict[str, list[register_value_type]]:
        """Return extra derived channel data for a single register read.

        Default returns empty dict. Override to add protocol-specific derived channels
        (e.g., bitmap bit extraction for Modbus uint16 registers).
        """
        return {}


class InstroRegisterInstrument(Instrument):
    """High-level driver abstraction to communicate with register-based instruments.

    Methods return Measurement and Command data types to be compatible with Nominal Instrumentation
    tools. Alternatively, specific driver implementations may be used which expose register values directly.
    """

    _driver: RegisterDriverBase
    name: str

    def __init__(
        self,
        driver: RegisterDriverBase,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """Initialize a InstroRegisterInstrument instance.

        Args:
            name (str): A name to identify this power supply instance. Used in channel naming
                and published data.
            driver (RegisterDriverBase): Driver instance for the specific register protocol or device.
                Concrete drivers own their transport setup::

                    device = InstroRegisterInstrument(
                        name="my device",
                        driver=ModbusRegisterDriver(ModbusConfig, thread_safe=True),
                    )
                If client applications utilize the background process by executing InstroRegisterInstrument.start()
                while simultaneously using this api in another thread by calling read/write, it is expected that
                the underlying driver for this Instrument is thread-safe

            publishers (list[Publisher] | None, optional): List of publishers to send data to
                when executing methods. Defaults to None.
            **kwargs: Optional keyword arguments used as tags throughout the life of the instrument.
                These tags are applied to the Measurement and Command objects and can be utilized by publishers like
                NominalCorePublisher as added metadata.

                Special keyword arguments:
                    dataset_rid (str): If provided, automatically creates and adds a
                        NominalCorePublisher with the specified dataset RID. Assumes a Nominal
                        'default' credential is stored on disk.
        """
        self.name = driver.device_name
        super().__init__(self.name, connection_config=None, publishers=publishers, **kwargs)

        self._driver = driver
        self._define_background_daemon()

    def open(self):
        """Establish connection to the device."""
        logger.info("Opening register device '%s'", self.name)
        super().open()
        self._driver.open()
        logger.info("Opened register device '%s'", self.name)

    def close(self):
        """Disconnect from the device."""
        logger.info("Closing register device '%s'", self.name)
        super().close()
        self._driver.close()
        logger.info("Closed register device '%s'", self.name)

    def read(self, register_id: str, **kwargs) -> Measurement:
        """Read a register directly from the device."""
        timestamp = time.time_ns()
        raw, scaled = self._driver.read_raw_scaled(register_id)
        channel_data: dict[str, list[register_value_type]] = {f"{self.name}.{register_id}": [scaled]}
        extra = self._driver.build_extra_channels(register_id, raw, scaled)
        channel_data.update({f"{self.name}.{k}": v for k, v in extra.items()})
        meas = Measurement(
            channel_data=channel_data,
            timestamps=[timestamp],
            tags={**self.default_tags, **(kwargs or {})},
        )
        self.publish(meas)
        return meas

    def write(self, register_id: str, value: float | int | str, **kwargs) -> Command:
        """Write a register directly to the device."""
        timestamp = time.time_ns()
        actual_value = self._driver.write(register_id, value)
        cmd = Command(
            channel_data={f"{self.name}.{register_id}.cmd": actual_value},
            timestamp=timestamp,
            tags={**self.default_tags, **(kwargs or {})},
        )
        self.publish(cmd)
        return cmd

    def read_group(self, group_id: str, **kwargs) -> Measurement:
        """Read a register group directly from the device."""
        timestamp = time.time_ns()
        group_registers = self._driver.enumerate_group_registers(group_id)
        _, scaled = self._driver.read_group_raw_scaled(group_id)
        meas_data = {
            f"{self.name}.{register_id}": [reg_scaled] for register_id, reg_scaled in zip(group_registers, scaled)
        }
        meas = Measurement(
            channel_data=meas_data,
            timestamps=[timestamp],
            tags={**self.default_tags, **(kwargs or {})},
        )
        self.publish(meas)
        return meas

    def write_group(self, group_id: str, values: list[float | int | str], **kwargs) -> Command:
        """Write a register group directly to the device.

        Note that some driver implementations may have a different definition of register.
        Ex: A single value read by this API could be spread across multiple modbus registers.
        """
        timestamp = time.time_ns()
        group_registers = self._driver.enumerate_group_registers(group_id)
        actual_values = self._driver.write_group(group_id, values)
        cmd = Command(
            channel_data={
                f"{self.name}.{register_id}.cmd": reg_value
                for register_id, reg_value in zip(group_registers, actual_values)
            },
            timestamp=timestamp,
            tags={**self.default_tags, **(kwargs or {})},
        )
        self.publish(cmd)
        return cmd

    def _define_background_daemon(self) -> None:
        """Register daemon polling: one call per read_group; individual reads for ungrouped registers."""
        grouped_register_names: set[str] = set()
        for group_id in self._driver.readable_groups:
            self.add_background_daemon_function(self.read_group, group_id)
            grouped_register_names.update(self._driver.enumerate_group_registers(group_id))

        for reg in self._driver.readable_registers:
            if reg.poll and reg.name not in grouped_register_names:
                self.add_background_daemon_function(self.read, reg.name)
