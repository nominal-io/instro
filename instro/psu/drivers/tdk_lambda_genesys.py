"""TDK Lambda Genesys-family PSU driver (single-channel)."""

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase


class TDKLambdaGenesys(PSUDriverBase):
    """TDK Lambda Genesys-family single-channel PSU."""

    FRIENDLY_NAME = "TDK Lambda Genesys-family PSU"

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

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
        self._write_checked(f"CURR {current_limit:.3f}")

    def get_current(self, channel: int) -> float:
        self._require_channel(channel)
        return self._query_checked_float("MEAS:CURR?")

    def output_enable(self, enable: bool, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked("OUTP:STAT ON" if enable else "OUTP:STAT OFF")

    def get_output_status(self, channel: int) -> bool:
        self._require_channel(channel)
        return self._query_checked_bool("OUTP:STAT?")

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked(f"VOLT:PROT:LEV {voltage:.3f}")

    def get_overvoltage_protection_level(self, channel: int) -> float:
        self._require_channel(channel)
        return self._query_checked_float("VOLT:PROT:LEV?")

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._require_channel(channel)
        raise FeatureNotSupportedError(
            f"set_overvoltage_protection_enabled is not supported by the {self.FRIENDLY_NAME}"
        )

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        self._require_channel(channel)
        raise FeatureNotSupportedError(
            f"get_overvoltage_protection_enabled is not supported by the {self.FRIENDLY_NAME}"
        )

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"set_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}")

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"get_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}")

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"set_overcurrent_protection_level is not supported by the {self.FRIENDLY_NAME}")

    def get_overcurrent_protection_level(self, channel: int) -> float:
        self._require_channel(channel)
        raise FeatureNotSupportedError(f"get_overcurrent_protection_level is not supported by the {self.FRIENDLY_NAME}")

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._require_channel(channel)
        self._write_checked("CURR:PROT:STAT ON" if enabled else "CURR:PROT:STAT OFF")

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

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _query_checked_float(self, command: str) -> float:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
            return float(value)

    def _query_checked_bool(self, command: str) -> bool:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
        return value.strip().upper() in {"1", "ON"}

    def _check_errors(self) -> None:
        err = self._visa.query("SYSTEM:ERROR?")
        code = err.strip().split(",", 1)[0].lstrip("+").strip()
        if code != "0":
            raise RuntimeError(f"The {self.FRIENDLY_NAME} reported error: {err.strip()}")
