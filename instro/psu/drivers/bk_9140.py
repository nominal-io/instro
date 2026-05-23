"""B&K Precision 9140-series PSU driver. SCPI surface is shared with other multi-channel B&K models."""

from instro.psu import PSUDriverBase
from instro.utils.transports.visa import VisaConfig, VisaDriver


class BK9140(PSUDriverBase):
    """B&K Precision 9140-series multi-channel PSU."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._active_channel: int | None = None

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def _channel_select_locked(self, channel: int) -> None:
        """Select channel on the instrument; caller must hold the VISA lock."""
        if channel != self._active_channel:
            self._visa.write(f"INST {channel - 1}")
            self._active_channel = channel

    def set_voltage(self, voltage: float, channel: int = 1) -> None:
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write(f"VOLT {voltage:.3f}")
            self._check_errors()

    def get_voltage(self, channel: int = 1) -> float:
        with self._visa.lock():
            self._channel_select_locked(channel)
            value = self._visa.query("MEAS:VOLT?")
            self._check_errors()
            return float(value)

    def set_current_limit(self, current_limit: float, channel: int = 1) -> None:
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write(f"CURR {current_limit:.3f}")
            self._check_errors()

    def get_current(self, channel: int = 1) -> float:
        with self._visa.lock():
            self._channel_select_locked(channel)
            value = self._visa.query("MEAS:CURR?")
            self._check_errors()
            return float(value)

    def output_enable(self, enable: bool, channel: int = 1) -> None:
        cmd = "OUTP:STAT ON" if enable else "OUTP:STAT OFF"
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write(cmd)
            self._check_errors()

    def get_output_status(self, channel: int = 1) -> bool:
        with self._visa.lock():
            self._channel_select_locked(channel)
            resp = self._visa.query("OUTP:STAT?")
            self._check_errors()
        return resp == "1"

    def _check_errors(self) -> None:
        err = self._visa.query("SYST:ERR?")
        if not err.startswith("0"):
            raise RuntimeError(f"BK PSU reported error: {err}")
