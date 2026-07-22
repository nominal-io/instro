"""EA Elektro-Automatik PSB/PSBE bidirectional DC supply driver (source and sink over SCPI)."""

import string

from instro.eload import ELoadDriverBase, LoadMode, SlewRateDirection
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase

FRIENDLY_NAME = "EA PSB/PSBE"

_SINK_CMD: dict[LoadMode, str] = {
    LoadMode.CC: "SINK:CURR",
    LoadMode.CP: "SINK:POW",
    LoadMode.CR: "SINK:RES",
}


class EAPSB(PSUDriverBase, ELoadDriverBase):
    """EA PSB/PSBE bidirectional line (PSB 9000/10000, PSBE 9000/10000); SCPI surface shared across the line per REV 24."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()
        self._write_checked("SYST:LOCK ON")
        owner = self._visa.query("SYST:LOCK:OWN?").strip()
        if owner != "REMOTE":
            raise RuntimeError(f"{FRIENDLY_NAME} did not grant the remote lock (owner: {owner})")

    def close(self) -> None:
        self._write_checked("SYST:LOCK OFF")
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"VOLT {voltage:.3f}")

    def get_voltage(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float("MEAS:VOLT?")

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"CURR {current_limit:.3f}")

    def get_current(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float("MEAS:CURR?")

    def output_enable(self, enable: bool, channel: int) -> None:
        _check_channel(channel)
        self._write_checked("OUTP ON" if enable else "OUTP OFF")

    def get_output_status(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            resp = self._visa.query("OUTP?")
            self._check_errors()
        return resp.strip().upper() == "ON"

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"VOLT:PROT {voltage:.3f}")

    def get_overvoltage_protection_level(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float("VOLT:PROT?")

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_overvoltage_protection_enabled is not supported by the {FRIENDLY_NAME}")

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(f"get_overvoltage_protection_enabled is not supported by the {FRIENDLY_NAME}")

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_overvoltage_protection_delay is not supported by the {FRIENDLY_NAME}")

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        raise FeatureNotSupportedError(f"get_overvoltage_protection_delay is not supported by the {FRIENDLY_NAME}")

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"CURR:PROT {current:.3f}")

    def get_overcurrent_protection_level(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float("CURR:PROT?")

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_overcurrent_protection_enabled is not supported by the {FRIENDLY_NAME}")

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(f"get_overcurrent_protection_enabled is not supported by the {FRIENDLY_NAME}")

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_remote_sense_enabled is not supported by the {FRIENDLY_NAME}")

    def get_remote_sense_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(f"get_remote_sense_enabled is not supported by the {FRIENDLY_NAME}")

    def set_mode(self, mode: LoadMode, channel: int) -> None:
        _check_channel(channel)
        self._write_checked("SYST:CONF:MODE UIR" if mode is LoadMode.CR else "SYST:CONF:MODE UIP")

    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        _check_channel(channel)
        if mode is LoadMode.CV:
            self._write_checked(f"VOLT {value:.3f}")
            if curr_limit is not None:
                self._write_checked(f"SINK:CURR {curr_limit:.3f}")
        else:
            self._write_checked(f"{_SINK_CMD[mode]} {value:.3f}")

    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_range is not supported by the {FRIENDLY_NAME}")

    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_slewrate is not supported by the {FRIENDLY_NAME}")

    def short_output(self, enable: bool, channel: int) -> None:
        raise FeatureNotSupportedError(f"short_output is not supported by the {FRIENDLY_NAME}")

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _query_checked_float(self, command: str) -> float:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
            return float(value.strip().rstrip(string.ascii_letters))

    def _check_errors(self) -> None:
        err = self._visa.query("SYST:ERR?")
        if not err.startswith("0"):
            raise RuntimeError(f"EA PSB reported error: {err}")


def _check_channel(channel: int) -> None:
    if channel != 1:
        raise ValueError("EA PSB channel must be 1")
