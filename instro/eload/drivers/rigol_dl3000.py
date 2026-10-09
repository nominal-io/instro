"""Rigol DL3000-series E-Load driver. Covers DL3021/DL3021A/DL3031/DL3031A/DL3041 (shared SCPI surface).

Hardware-validated against the DL3021; other family members are expected to work but have not been bench-tested.
"""

from instro.eload import ELoadDriverBase
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver

_MODE_KEYWORDS = {
    LoadMode.CC: "CURR",
    LoadMode.CV: "VOLT",
    LoadMode.CP: "POW",
    LoadMode.CR: "RES",
}

_SLEW_COMMANDS = {
    SlewRateDirection.BOTH: ":SOUR:CURR:SLEW",
    SlewRateDirection.RISE: ":SOUR:CURR:SLEW:POS",
    SlewRateDirection.FALL: ":SOUR:CURR:SLEW:NEG",
}


class RigolDL3000(ELoadDriverBase):
    """Rigol DL3000-series single-channel E-Load (DL3021/DL3021A/DL3031/DL3031A/DL3041)."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def short_output(self, enable: bool, channel: int) -> None:
        raise FeatureNotSupportedError(
            "short_output is not supported by the Rigol DL3000: the programming guide has no short-circuit command"
        )

    def set_mode(self, mode: LoadMode, channel: int) -> None:
        self._write_checked(f":SOUR:FUNC {_MODE_KEYWORDS[mode]}")

    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        """Set the level for ``mode``; in CV mode, a ``curr_limit`` is written first as the CV current limit."""
        if mode is LoadMode.CV and curr_limit is not None:
            self._write_checked(f":SOUR:VOLT:ILIM {curr_limit}")
        self._write_checked(f":SOUR:{_MODE_KEYWORDS[mode]} {value}")

    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        if mode is LoadMode.CP:
            raise FeatureNotSupportedError("The Rigol DL3000 has no range in CP mode")
        self._write_checked(f":SOUR:{_MODE_KEYWORDS[mode]}:RANG {value}")

    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        self._write_checked(f"{_SLEW_COMMANDS[direction]} {rate}")

    def output_enable(self, enable: bool, channel: int) -> None:
        self._write_checked(f":SOUR:INP {'ON' if enable else 'OFF'}")

    def get_current(self, channel: int) -> float:
        return self._query_checked_float(":MEAS:CURR?")

    def get_voltage(self, channel: int) -> float:
        return self._query_checked_float(":MEAS:VOLT?")

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
        err = self._visa.query(":SYST:ERR?")
        code = err.strip().split(",", 1)[0].lstrip("+")
        if code != "0":
            raise RuntimeError(f"The Rigol DL3000 reported error: {err.strip()}")
