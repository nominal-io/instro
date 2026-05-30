"""I2C instrument interface and driver contract."""

from __future__ import annotations

import abc
import logging
import threading
import time
from typing import Literal, Optional

from instro.i2c.types import CommandDevice, RegisterDevice, SystemDefinition
from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher

logger = logging.getLogger(__name__)


class I2CDriverBase(abc.ABC):
    """Vendor I2C-adapter driver contract. Concrete drivers own their transport and lifecycle.

    ``address`` arguments are 7-bit device addresses (the adapter SDK handles
    the R/W bit). Drivers translate this contract to the vendor SDK's master
    primitives.
    """

    @abc.abstractmethod
    def open(self) -> None:
        """Open the adapter (typically a USB host adapter) and prepare it for transactions."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the adapter handle. Idempotent."""

    @abc.abstractmethod
    def read(self, address: int, length: int) -> bytes:
        """Read ``length`` bytes from the 7-bit ``address`` (master read transaction)."""

    @abc.abstractmethod
    def write(self, address: int, data: bytes) -> None:
        """Write ``data`` to the 7-bit ``address`` (master write transaction)."""

    @abc.abstractmethod
    def write_read(self, address: int, data: bytes, read_len: int) -> bytes:
        """Write ``data`` then immediately read ``read_len`` bytes from ``address`` using a repeated START.

        Used for register reads: write the register address, then read its
        contents without releasing the bus. No STOP is issued between the
        write and the read.
        """

    @abc.abstractmethod
    def set_bitrate(self, bitrate: int) -> None:
        """Set the master clock rate in kHz. Drivers may snap to the nearest supported rate."""

    @abc.abstractmethod
    def set_pullups(self, enable: bool) -> None:
        """Enable or disable the adapter's internal bus pull-ups (typical: ~10 kΩ to VCC)."""

    @abc.abstractmethod
    def set_power_enable(self, enable: bool) -> None:
        """Enable or disable target-power output on the adapter (where supported)."""


class I2CInterface(Instrument):
    """I2C interface supporting register-based and command-based devices.

    Commands are enum values OR'd together and sent as a single byte stream.
    The ``SystemDefinition`` is read-only — ``I2CInterface`` resolves names to
    transactions on every call; the vendor driver never sees it.
    """

    def __init__(
        self,
        name: str,
        driver: I2CDriverBase,
        system_definition: SystemDefinition,
        publishers: Optional[list[Publisher]] = None,
        **kwargs,
    ):
        """Initialize an I2CInterface.

        Args:
            name: Channel-name prefix for published data.
            driver: Concrete I2C-adapter driver; owns its own transport::

                i2c = I2CInterface(
                    "main",
                    driver=Aardvark(serial_number="2239-764425"),
                    system_definition=system,
                )

            system_definition: Bus description (devices, register maps, comms params).
            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher
                (uses the on-disk 'default' Nominal credential).
        """
        super().__init__(name=name, publishers=publishers, **kwargs)

        self._driver = driver
        self._sysdef = system_definition
        self._resource_lock = threading.Lock()

    @staticmethod
    def _addr_prefix(addr_width_bytes: int, reg_addr: int) -> bytes:
        """Encode ``reg_addr`` as a big-endian ``addr_width_bytes`` byte prefix (1 or 2 bytes)."""
        if addr_width_bytes == 1:
            return bytes([reg_addr & 0xFF])
        if addr_width_bytes == 2:
            return reg_addr.to_bytes(2, "big")  # register index byte order
        raise ValueError("addr_width_bytes must be 1 or 2")

    def write_read_raw(self, address: int, payload: bytes, length: int, endianness: Literal["little", "big"]) -> int:
        """Write-then-read without an intermediate I2C STOP (uses repeated START); decode as integer.

        Typical use: write a register address, then read its contents.

        Args:
            address: 7-bit I2C device address.
            payload: Bytes to write (typically the register address).
            length: Number of bytes to read back.
            endianness: Byte order for the integer decode.
        """
        data = self._driver.write_read(address, payload, length)

        return int.from_bytes(data, endianness)

    def write_raw(self, address: int, data: bytes):
        """Write ``data`` to the 7-bit I2C ``address``."""
        self._driver.write(address, data)

    def read_raw(self, address: int, length: int, endianness: Literal["little", "big"]) -> int:
        """Read ``length`` bytes from the 7-bit I2C ``address`` and decode as an integer."""
        data = self._driver.read(address, length)

        return int.from_bytes(data, endianness)

    def write_then_read_raw(
        self, address: int, payload: bytes, length: int, endianness: Literal["little", "big"]
    ) -> int:
        """Write-then-read with an I2C STOP between operations (required by devices that need STOP to process input)."""
        with self._resource_lock:
            self.write_raw(address=address, data=payload)
            response = self.read_raw(address=address, length=length, endianness=endianness)

        return response

    @publish_measurement
    def read(self, peripheral: str, register_alias: str, field: str = "", **kwargs) -> Measurement:
        """Read a register (or one of its named fields) on ``peripheral``.

        With ``field``, only the named bit field is extracted (masked and shifted).
        Raises ``ValueError`` if ``peripheral`` is not a register-based device.
        """
        device = self._sysdef.device(peripheral)
        if not isinstance(device, RegisterDevice):
            raise ValueError(f"Device '{peripheral}' is not a register-based device")

        reg_def = device.register(register_alias)
        prefix = self._addr_prefix(device.addr_width_bytes, reg_def.register)
        # Legacy I2C used underscores between every component instead of dots.
        sep = "_" if self.legacy_naming else "."
        channel_name = f"{device.name}{sep}{reg_def.alias}"

        response = self.write_read_raw(
            address=device.address, payload=prefix, length=reg_def.data_width_bytes, endianness=reg_def.endianness
        )

        timestamp = time.time_ns()

        data: float | int
        if field:
            field_def = reg_def.field(field)
            data = (response & field_def.mask()) >> field_def.lsb
            channel_name += f"{sep}{field}"
        else:
            # Use unified format conversion
            data = reg_def.float_from_raw(response)

        if self.legacy_naming:
            # Legacy I2C published `{name}_{periph}_{reg}[_{field}]` — fully underscore-separated,
            # including between the instrument name and the rest. Bypass `_package_measurement`
            # because it always joins with a dot.
            return Measurement(
                channel_data={f"{self.name}_{channel_name}": [float(data)]},
                timestamps=[timestamp],
                tags={**self.default_tags, **(kwargs or {})},
            )
        return self._package_measurement(channel_name, data, timestamp, **kwargs)

    @publish_command
    def write(self, peripheral: str, register_alias: str, value: int, field: str = "", **kwargs) -> Command:
        """Write a register (or one of its named fields) on ``peripheral``.

        With ``field``, performs a thread-safe read-modify-write of just that
        field (mask, insert, write). Raises ``ValueError`` if ``peripheral`` is
        not a register-based device.
        """
        device = self._sysdef.device(peripheral)
        if not isinstance(device, RegisterDevice):
            raise ValueError(f"Device '{peripheral}' is not a register-based device")

        reg_def = device.register(register_alias)
        prefix = self._addr_prefix(device.addr_width_bytes, reg_def.register)

        data = value
        sep = "_" if self.legacy_naming else "."
        channel_name = f"{device.name}{sep}{reg_def.alias}"
        logger.debug("Sending I2C write command to '%s' for peripheral '%s'", self.name, peripheral)

        with self._resource_lock:
            if field:
                channel_name += f"{sep}{field}"

                # 1. Obtain the definition of the field to update from the register definition.
                field_def = reg_def.field(field)
                # 2. Read the current value of the register from the hardware.
                current_value = self.write_read_raw(
                    address=device.address,
                    payload=prefix,
                    length=reg_def.data_width_bytes,
                    endianness=reg_def.endianness,
                )
                # 3. Clear (mask out) the bits corresponding to the field in the register.
                data = current_value & ~field_def.mask()
                # 4. Insert the new field value in the correct position, preserving other register bits.
                data |= (value << field_def.lsb) & field_def.mask()

            payload = data.to_bytes(reg_def.data_width_bytes, reg_def.endianness)  # type: ignore[arg-type]

            self._driver.write(address=device.address, data=prefix + payload)

        timestamp = time.time_ns()

        if self.legacy_naming:
            # Legacy I2C published `{name}_{periph}_{reg}[_{field}]_cmd` — fully underscore-separated.
            return Command(
                channel_data={f"{self.name}_{channel_name}_cmd": float(value)},
                timestamp=timestamp,
                tags={**self.default_tags, **(kwargs or {})},
            )
        return self._package_command(f"{channel_name}.cmd", value, timestamp, **kwargs)

    def reset_reg(self, peripheral: str, register_alias: str, **kwargs):
        """Write a register's defined default value back to hardware (initialization/recovery)."""
        device = self._sysdef.device(peripheral)
        if not isinstance(device, RegisterDevice):
            raise ValueError(f"Device '{peripheral}' is not a register-based device")

        reg_def = device.register(register_alias)
        default = reg_def.default_value

        self.write(peripheral, register_alias, default, **kwargs)

    @publish_measurement
    def query(self, peripheral: str, batch_command: str, **kwargs) -> Measurement:
        """Send a batch command (OR of its enum members) to ``peripheral`` and read the response.

        Uses write-then-read with an I2C STOP between operations. Raises
        ``ValueError`` if ``peripheral`` is not a command-based device, or if
        ``batch_command`` references undefined command enums.
        """
        device = self._sysdef.device(peripheral)
        if not isinstance(device, CommandDevice):
            raise ValueError(f"Device '{peripheral}' is not a command-based device")

        # OR all the command enum values together
        command_value = 0
        for cmd_enum in device.batch_commands[batch_command]:
            command_value |= cmd_enum.value

        # Convert command to bytes (assuming 8-bit commands, adjust if needed)
        command_bytes = command_value.to_bytes(1, device.endianness)
        logger.debug("Sending I2C query command to '%s' for peripheral '%s'", self.name, peripheral)

        response = self.write_then_read_raw(
            address=device.address,
            payload=command_bytes,
            length=device.data_width_bytes,
            endianness=device.endianness,
        )

        # Use unified format conversion
        scaled_value = device.data_format.float_from_raw(response)

        timestamp = time.time_ns()

        if self.legacy_naming:
            # Legacy I2C published `{name}_{periph}_{batch_command}` — fully underscore-separated.
            return Measurement(
                channel_data={f"{self.name}_{device.name}_{batch_command}": [float(scaled_value)]},
                timestamps=[timestamp],
                tags={**self.default_tags, **(kwargs or {})},
            )
        return self._package_measurement(f"{device.name}.{batch_command}", scaled_value, timestamp, **kwargs)

    def open(self):
        """Open the underlying I2C-adapter driver."""
        logger.info("Opening I2C instrument '%s'", self.name)
        self._driver.open()
        logger.info("Opened I2C instrument '%s'", self.name)

    def close(self):
        """Close the underlying driver and stop the daemon."""
        logger.info("Closing I2C instrument '%s'", self.name)
        super().close()
        self._driver.close()
        logger.info("Closed I2C instrument '%s'", self.name)
