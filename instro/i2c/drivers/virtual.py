"""Virtual I2C adapter driver: an in-memory bus simulated in-process, with no SDK or hardware."""

from __future__ import annotations

from collections.abc import Mapping

from instro.i2c import I2CDriverBase


class VirtualI2C(I2CDriverBase):
    """I2C adapter simulated in-process: an in-memory bus with no SDK or hardware.

    Each peripheral is a register-pointer device. A write's leading bytes (its
    register-address width, 1 by default) set the pointer and the rest are stored as
    that register's value; a read returns the bytes stored at the pointer, zero-padded
    or truncated to the requested length. So `I2CInterface` register and field writes
    read back, ``reset_reg`` restores defaults, and a command-based device's ``query``
    reads back whatever is stored at its command byte. Bitrate, pull-up, and
    target-power settings are accepted and have no effect.
    """

    def __init__(
        self,
        peripherals: Mapping[int, Mapping[int, bytes]] | None = None,
        addr_width_bytes: Mapping[int, int] | None = None,
    ) -> None:
        r"""Create a virtual I2C adapter.

        Args:
            peripherals (Mapping[int, Mapping[int, bytes]] | None): The bus contents: each
                7-bit address maps to its initial register values (register address to the
                bytes it holds). Only these addresses answer; any other raises ``OSError``,
                as a real adapter does on a NAK. ``None`` makes every address answer, with
                every register reading zero until written.
            addr_width_bytes (Mapping[int, int] | None): Register-address width in bytes
                (1 or 2) per 7-bit address, matching ``RegisterDevice.addr_width_bytes``.
                Addresses left out use 1.

        Example::

            from instro.i2c import I2CInterface
            from instro.i2c.drivers import VirtualI2C
            from instro.i2c.types import RegisterDef, RegisterDevice, SystemDefinition

            gpio = RegisterDevice(name="gpio", address=0x20, registers={"OUTPUT": RegisterDef("OUTPUT", 0x01)})
            system = SystemDefinition(devices={gpio.name: gpio})

            driver = VirtualI2C({0x20: {0x01: b"\x0f"}})  # optional: which peripherals answer
            with I2CInterface("i2c", driver=driver, system_definition=system) as i2c:
                print(i2c.read("gpio", "OUTPUT").latest)  # 15.0
                i2c.write("gpio", "OUTPUT", 0xFF)
                print(i2c.read("gpio", "OUTPUT").latest)  # 255.0
        """
        self._open_bus = peripherals is None
        self._registers: dict[int, dict[int, bytes]] = {
            address: dict(registers) for address, registers in (peripherals or {}).items()
        }
        self._pointers: dict[int, int] = {}
        self._addr_width_bytes: dict[int, int] = dict(addr_width_bytes or {})

    def open(self) -> None:
        """Nothing to connect to."""

    def close(self) -> None:
        """Nothing to disconnect."""

    def read(self, address: int, length: int) -> bytes:
        registers = self._peripheral(address)
        value = registers.get(self._pointers.get(address, 0), b"")
        return value[:length].ljust(length, b"\x00")

    def write(self, address: int, data: bytes) -> None:
        registers = self._peripheral(address)
        width = self._addr_width_bytes.get(address, 1)
        if len(data) < width:
            raise ValueError(f"Write to I2C address 0x{address:02X} is shorter than its {width}-byte register address")
        pointer = int.from_bytes(data[:width], "big")
        self._pointers[address] = pointer
        if len(data) > width:
            registers[pointer] = bytes(data[width:])

    def write_read(self, address: int, data: bytes, read_len: int) -> bytes:
        self.write(address, data)
        return self.read(address, read_len)

    def set_bitrate(self, bitrate: int) -> None:
        """Accepted; no effect."""

    def set_pullups(self, enable: bool) -> None:
        """Accepted; no effect."""

    def set_power_enable(self, enable: bool) -> None:
        """Accepted; no effect."""

    def _peripheral(self, address: int) -> dict[int, bytes]:
        if address not in self._registers:
            if not self._open_bus:
                raise OSError(f"No peripheral at I2C address 0x{address:02X} (NAK)")
            self._registers[address] = {}
        return self._registers[address]
