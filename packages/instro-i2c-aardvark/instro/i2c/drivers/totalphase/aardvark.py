"""Total Phase Aardvark I2C/SPI host-adapter driver (``pyaardvark`` SDK, not VISA)."""

from __future__ import annotations

from instro.i2c import I2CDriverBase


class Aardvark(I2CDriverBase):
    """Total Phase Aardvark I2C/SPI host adapter. Connection params captured in ``__init__``; USB opens on ``open()``."""

    def __init__(self, serial_number: str | None = None) -> None:
        """``serial_number`` (e.g. ``"2239-764425"``); ``None`` lets ``pyaardvark`` pick the first available adapter."""
        self._serial_number = serial_number
        self._device = None

    def open(self) -> None:
        import pyaardvark  # type: ignore[import-untyped]

        self._device = pyaardvark.open(serial_number=self._serial_number)

    def close(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None

    def read(self, address: int, length: int) -> bytes:
        return self._require_device().i2c_master_read(address, length)

    def write(self, address: int, data: bytes) -> None:
        self._require_device().i2c_master_write(address, data)

    def write_read(self, address: int, data: bytes, read_len: int) -> bytes:
        return self._require_device().i2c_master_write_read(address, data, read_len)

    def set_bitrate(self, bitrate: int) -> None:
        self._require_device().i2c_bitrate = bitrate

    def set_pullups(self, enable: bool) -> None:
        self._require_device().i2c_pullups = enable

    def set_power_enable(self, enable: bool) -> None:
        self._require_device().target_power = enable

    def _require_device(self):
        if self._device is None:
            raise RuntimeError("Aardvark driver is not open; call open() first")
        return self._device
