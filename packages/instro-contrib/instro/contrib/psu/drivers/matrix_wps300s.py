"""Matrix WPS300S-series programmable DC power supply driver."""

import time

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import SerialConfig, TerminatorConfig, VisaConfig, VisaDriver
from instro.psu import PSUDriverBase


class MatrixWPS300S(PSUDriverBase):
    """Matrix WPS300S-series single-channel programmable DC PSU.

    Tested against the WPS300S-150-5 (0–150 V, 0–5 A, 300 W).
    Protocol: SCPI over RS-232, 9600 baud 8-N-1, CRLF termination

    """

    FRIENDLY_NAME = "Matrix WPS300S-series PSU"

    def __init__(self, visa_resource: str | VisaConfig, op_interval: float | None = 0.2) -> None:
        if isinstance(visa_resource, VisaConfig):
            visaConf = visa_resource
        else:
            visaConf = VisaConfig(
                visa_resource=visa_resource,
                serial_config=SerialConfig(baud_rate=9600),
                terminator=TerminatorConfig(read="\r\n", write="\r\n"),
            )
        self._visa = VisaDriver(visaConf)
        self._op_interval = op_interval
        self._last_op_time: float = 0.0

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked(f"VOLT {voltage:.3f}")

    def get_voltage(self, channel: int) -> float:
        self._require_channel(channel)
        return self._query_checked_float("MEAS:VOLT?")

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked(f"CURR {current_limit:.4f}")

    def get_current(self, channel: int) -> float:
        self._require_channel(channel)
        return self._query_checked_float("MEAS:CURR?")

    def output_enable(self, enable: bool, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked("OUTP ON" if enable else "OUTP OFF")

    def get_output_status(self, channel: int) -> bool:
        self._require_channel(channel)
        return self._query_checked_bool("OUTP?")

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked(f"VOLT:PROT {voltage:.3f}")

    def get_overvoltage_protection_level(self, channel: int) -> float:
        self._require_channel(channel)
        return self._query_checked_float("VOLT:PROT:LEV?")

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked(f"VOLT:PROT:STAT {'ON' if enabled else 'OFF'}")

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        self._require_channel(channel)
        return self._query_checked_bool("VOLT:PROT:STAT?")

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"set_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}")

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"get_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}")

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked(f"CURR:PROT {current:.4f}")

    def get_overcurrent_protection_level(self, channel: int) -> float:
        self._require_channel(channel)
        return self._query_checked_float("CURR:PROT?")

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked(f"CURR:PROT:STAT {'ON' if enabled else 'OFF'}")

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        self._require_channel(channel)
        return self._query_checked_bool("CURR:PROT:STAT?")

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"set_remote_sense_enabled is not supported by the {self.FRIENDLY_NAME}")

    def get_remote_sense_enabled(self, channel: int) -> bool:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"get_remote_sense_enabled is not supported by the {self.FRIENDLY_NAME}")

    def _require_channel(self, channel: int) -> None:
        if channel != 1:
            raise ValueError(f"The {self.FRIENDLY_NAME} supports only channel 1")

    def _throttle(self) -> None:
        # device needs a gap between every bus operation at 9600 baud
        if not self._op_interval:
            return
        elapsed = time.monotonic() - self._last_op_time
        if elapsed < self._op_interval:
            time.sleep(self._op_interval - elapsed)
        self._last_op_time = time.monotonic()

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._throttle()
            self._visa.write(command)
            self._check_errors()

    def _query_checked_float(self, command: str) -> float:
        with self._visa.lock():
            self._throttle()
            value = self._visa.query(command)
            self._check_errors()
            return float(value)

    def _query_checked_bool(self, command: str) -> bool:
        with self._visa.lock():
            self._throttle()
            value = self._visa.query(command)
            self._check_errors()
        return value.strip().upper() in {"1", "ON"}

    def _check_errors(self) -> None:
        self._throttle()
        err = self._visa.query("SYST:ERR?")
        if err.lower() != "no error":
            raise RuntimeError(f"The {self.FRIENDLY_NAME} reported error: {err.strip()}")
