"""B&K Precision 914X-series PSU driver."""

import dataclasses
import time

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import TerminatorConfig, VisaConfig, VisaDriver
from instro.psu import PSUDriverBase

FRIENDLY_NAME = "B&K Precision 914X-series PSU"

# The 914X LAN port speaks raw SOCKET and is LF-terminated; the VisaConfig default
# CR/LF write terminator gets rejected. LF is also correct over USB/INSTR.
_TERMINATOR = TerminatorConfig(read="\n", write="\n")


class BK914X(PSUDriverBase):
    """B&K Precision 914X-series multi-channel PSU."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        config = VisaConfig(visa_resource=visa_resource) if isinstance(visa_resource, str) else visa_resource
        self._visa = VisaDriver(dataclasses.replace(config, terminator=_TERMINATOR))
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

    def set_voltage(self, voltage: float, channel: int) -> None:
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write(f"VOLT {voltage:.3f}")
            self._check_errors()

    def get_voltage(self, channel: int) -> float:
        with self._visa.lock():
            self._channel_select_locked(channel)
            value = self._query_locked("MEAS:VOLT?")
            self._check_errors()
            return float(value)

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write(f"CURR {current_limit:.3f}")
            self._check_errors()

    def get_current(self, channel: int) -> float:
        with self._visa.lock():
            self._channel_select_locked(channel)
            value = self._query_locked("MEAS:CURR?")
            self._check_errors()
            return float(value)

    def output_enable(self, enable: bool, channel: int) -> None:
        cmd = "OUTP:STAT ON" if enable else "OUTP:STAT OFF"
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write(cmd)
            self._check_errors()

    def get_output_status(self, channel: int) -> bool:
        with self._visa.lock():
            self._channel_select_locked(channel)
            resp = self._query_locked("OUTP:STAT?")
            self._check_errors()
        return resp == "1"

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write(f"VOLT:PROT {voltage:.3f}")
            self._check_errors()

    def get_overvoltage_protection_level(self, channel: int) -> float:
        with self._visa.lock():
            self._channel_select_locked(channel)
            value = self._query_locked("VOLT:PROT?")
            self._check_errors()
            return float(value)

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_overvoltage_protection_enabled is not supported by the {FRIENDLY_NAME}")

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(f"get_overvoltage_protection_enabled is not supported by the {FRIENDLY_NAME}")

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_overvoltage_protection_delay is not supported by the {FRIENDLY_NAME}")

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        raise FeatureNotSupportedError(f"get_overvoltage_protection_delay is not supported by the {FRIENDLY_NAME}")

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write(f"CURR:PROT {current:.3f}")
            self._check_errors()

    def get_overcurrent_protection_level(self, channel: int) -> float:
        with self._visa.lock():
            self._channel_select_locked(channel)
            value = self._query_locked("CURR:PROT?")
            self._check_errors()
            return float(value)

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write("CURR:PROT:STAT ON" if enabled else "CURR:PROT:STAT OFF")
            self._check_errors()

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        with self._visa.lock():
            self._channel_select_locked(channel)
            value = self._query_locked("CURR:PROT:STAT?")
            self._check_errors()
        return value.strip().upper() in {"1", "ON"}

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        with self._visa.lock():
            self._channel_select_locked(channel)
            self._visa.write("VOLT:SENS ON" if enabled else "VOLT:SENS OFF")
            self._check_errors()

    def get_remote_sense_enabled(self, channel: int) -> bool:
        with self._visa.lock():
            self._channel_select_locked(channel)
            value = self._query_locked("VOLT:SENS?")
            self._check_errors()
        return value.strip().upper() in {"1", "ON"}

    def _query_locked(self, command: str) -> str:
        self._visa.write(command)
        response = ""
        for _ in range(3):
            time.sleep(0.05)
            response = self._visa.read_raw().decode().strip()
            if response.strip():
                return response
        return response

    def _check_errors(self) -> None:
        with self._visa.lock():
            err = self._query_locked("SYST:ERR?")
        if not err.startswith("0"):
            raise RuntimeError(f"BK914X PSU reported error: {err}")
