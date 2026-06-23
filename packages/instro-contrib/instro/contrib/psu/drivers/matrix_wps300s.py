"""Matrix WPS300S-series programmable DC power supply driver."""

import time

from pyvisa.errors import VisaIOError

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import SerialConfig, TerminatorConfig, VisaConfig, VisaDriver
from instro.psu import PSUDriverBase


class MatrixWPS300S(PSUDriverBase):
    """Matrix WPS300S-series single-channel programmable DC PSU.

    Tested against the WPS300S-150-5 (0–150 V, 0–5 A, 300 W).
    Protocol: SCPI over RS-232, 9600 baud 8-N-1.

    The PSU's UART silently drops characters if commands arrive back-to-back, so
    every read and write is paced through ``command_interval``.

    """

    FRIENDLY_NAME = "Matrix WPS300S-series PSU"
    _command_interval: float | None
    _last_io_time: float
    _visa: VisaDriver

    def __init__(self, visa_resource: str | VisaConfig, command_interval: float | None = 0.2) -> None:
        if isinstance(visa_resource, VisaConfig):
            visaConf = visa_resource
        else:
            visaConf = VisaConfig(
                visa_resource=visa_resource,
                serial_config=SerialConfig(baud_rate=9600),
                terminator=TerminatorConfig(read="\r\n", write="\r\n"),
            )
        self._visa = VisaDriver(visaConf)
        self._command_interval = command_interval
        self._last_io_time: float = 0.0

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int = 1) -> None:
        self._require_channel(channel)
        self._write(f"VOLT {voltage:.3f}")
        self._check_errors()

    def get_voltage(self, channel: int = 1) -> float:
        self._require_channel(channel)
        voltage = float(self._query("MEAS:VOLT?"))
        self._check_errors()
        return voltage

    def set_current_limit(self, current_limit: float, channel: int = 1) -> None:
        self._require_channel(channel)
        self._write(f"CURR {current_limit:.4f}")
        self._check_errors()

    def get_current(self, channel: int = 1) -> float:
        self._require_channel(channel)
        current = float(self._query("MEAS:CURR?"))
        self._check_errors()
        return current

    def output_enable(self, enable: bool, channel: int = 1) -> None:
        self._require_channel(channel)
        self._write("OUTP ON" if enable else "OUTP OFF")
        self._check_errors()

    def get_output_status(self, channel: int = 1) -> bool:
        self._require_channel(channel)
        status = self._query("OUTP?").strip().upper() in {"1", "ON"}
        self._check_errors()
        return status

    def set_overvoltage_protection_level(self, voltage: float, channel: int = 1) -> None:
        self._require_channel(channel)
        self._check_errors()
        self._write(f"VOLT:PROT {voltage:.3f}")

    def get_overvoltage_protection_level(self, channel: int = 1) -> float:
        self._require_channel(channel)
        level = float(self._query("VOLT:PROT?"))
        self._check_errors()
        return level

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int = 1) -> None:
        self._require_channel(channel)
        self._write(f"VOLT:PROT:STAT {'ON' if enabled else 'OFF'}")
        self._check_errors()

    def get_overvoltage_protection_enabled(self, channel: int = 1) -> bool:
        self._require_channel(channel)
        enabled = self._query("VOLT:PROT:STAT?").strip().upper() in {"1", "ON"}
        self._check_errors()
        return enabled

    def set_overvoltage_protection_delay(self, delay: float, channel: int = 1) -> None:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"set_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}")

    def get_overvoltage_protection_delay(self, channel: int = 1) -> float:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"get_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}")

    def set_overcurrent_protection_level(self, current: float, channel: int = 1) -> None:
        self._require_channel(channel)
        self._write(f"CURR:PROT {current:.4f}")
        self._check_errors()

    def get_overcurrent_protection_level(self, channel: int = 1) -> float:
        self._require_channel(channel)
        level = float(self._query("CURR:PROT?"))
        self._check_errors()
        return level

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int = 1) -> None:
        self._require_channel(channel)
        self._write(f"CURR:PROT:STAT {'ON' if enabled else 'OFF'}")
        self._check_errors()

    def get_overcurrent_protection_enabled(self, channel: int = 1) -> bool:
        self._require_channel(channel)
        enabled = self._query("CURR:PROT:STAT?").strip().upper() in {"1", "ON"}
        self._check_errors()
        return enabled

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"set_remote_sense_enabled is not supported by the {self.FRIENDLY_NAME}")

    def get_remote_sense_enabled(self, channel: int) -> bool:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"get_remote_sense_enabled is not supported by the {self.FRIENDLY_NAME}")

    def _throttle(self) -> None:
        if not self._command_interval:
            return
        call_time = time.monotonic()
        elapsed = call_time - self._last_io_time
        if elapsed < self._command_interval:
            time.sleep(self._command_interval - elapsed)

    def _write(self, command: str) -> None:
        self._throttle()
        try:
            self._visa.write(command)
        finally:
            self._last_io_time = time.monotonic()

    def _query(self, command: str) -> str:
        self._throttle()
        try:
            return self._visa.query(command)
        except VisaIOError:
            # A timed-out reply may still arrive in the OS buffer; drop it so the
            # next query doesn't read stale bytes and desync request/response.
            try:
                self._visa.clear()
            except Exception:
                pass
            raise
        finally:
            self._last_io_time = time.monotonic()

    def _require_channel(self, channel: int) -> None:
        if channel != 1:
            raise ValueError(f"The {self.FRIENDLY_NAME} supports only channel 1")

    def _check_errors(self) -> None:
        return
        # The logic below needs to be hardware-tested -- one user reported success, another reported console errors
        # err = self._query("SYST:ERR?")
        # if err.lower() != "no error":
        #     raise RuntimeError(f"The {self.FRIENDLY_NAME} reported error: {err.strip()}")
