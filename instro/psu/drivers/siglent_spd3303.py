"""Siglent SPD3303-series PSU driver."""

from instro.psu import PSUDriverBase
from instro.utils.transports.visa import VisaConfig, VisaDriver


class SiglentSPD3303(PSUDriverBase):
    """Siglent SPD3303-series PSU."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int = 1) -> None:
        self._write_checked(f"CH{channel}:VOLT {voltage:.3f}")

    def get_voltage(self, channel: int = 1) -> float:
        return self._query_checked_float(f"MEAS:VOLT? CH{channel}")

    def set_current_limit(self, current_limit: float, channel: int = 1) -> None:
        self._write_checked(f"CH{channel}:CURR {current_limit:.3f}")

    def get_current(self, channel: int = 1) -> float:
        return self._query_checked_float(f"MEAS:CURR? CH{channel}")

    def output_enable(self, enable: bool, channel: int = 1) -> None:
        cmd = f"OUTP CH{channel},ON" if enable else f"OUTP CH{channel},OFF"
        self._write_checked(cmd)

    def get_output_status(self, channel: int = 1) -> bool:
        return bool(self.query_status()[f"ch{channel}_enable"])

    def query_status(self) -> dict:
        """Query the status of the PSU (per-channel mode/enable + tracking mode)."""
        with self._visa.lock():
            resp = self._visa.query("SYST:STAT?")
            self._check_errors()
        return self._decode_status(int(resp, 16))

    def _decode_status(self, value: int) -> dict:
        return {
            "ch1_mode": "CC" if bool(value & 1) else "CV",
            "ch2_mode": "CC" if bool(value & 2) else "CV",
            "psu_mode": _psu_mode_to_str((value >> 2) & 3),
            "ch1_enable": bool(value & 16),
            "ch2_enable": bool(value & 32),
        }

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _query_checked_float(self, command: str) -> float:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
            return float(value)

    def _check_errors(self) -> None:
        err = self._visa.query("SYST:ERR?")
        if not err.startswith("+0"):
            raise RuntimeError(f"Siglent PSU reported error: {err}")


def _psu_mode_to_str(mode: int) -> str:
    match mode:
        case 1:
            return "INDEPENDENT"
        case 2:
            return "PARALLEL"
        case 3:
            return "SERIES"
        case _:
            return "UNDEFINED"
