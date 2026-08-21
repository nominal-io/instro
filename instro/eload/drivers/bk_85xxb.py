"""B&K Precision 8500B-series E-Load driver. Covers 8500B/8502B/8510B/8514B/8542B (shared SCPI surface).

Hardware-validated against the 8514B; other family members are expected to work but have not been bench-tested.
"""

from instro.eload import ELoadDriverBase
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports.visa import VisaConfig, VisaDriver


def loadmode_to_unit(mode: LoadMode) -> str:
    return {
        LoadMode.CC: "CURR",
        LoadMode.CV: "VOLT",
        LoadMode.CP: "POW",
        LoadMode.CR: "RES",
    }[mode]


class BK85XXB(ELoadDriverBase):
    """B&K Precision 8500B-series E-Load (8500B/8502B/8510B/8514B/8542B).

    Hardware-validated against the 8514B; others share the SCPI surface but have not been bench-tested.
    """

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()
        self._write_checked("SYST:REM")

    def close(self) -> None:
        self._visa.close()

    def short_output(self, enable: bool, channel: int) -> None:
        with self._visa.lock():
            self._visa.write(f"INPut:SHORt {int(enable)}")
            self._visa.write(f"INPut {int(enable)}")
            self._check_errors()

    def set_mode(self, mode: LoadMode, channel: int) -> None:
        self._write_checked(f"FUNCtion {loadmode_to_unit(mode)}")

    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        if mode is LoadMode.CV:
            pass  # TODO add CV→CC protection based off curr_limit
        self._write_checked(f"{loadmode_to_unit(mode)} {value}")

    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        if mode not in (LoadMode.CC, LoadMode.CV):
            raise NotImplementedError(
                f"BK85XXB only exposes :RANGe for CC and CV; {mode.value} is auto-ranged from the level value"
            )
        self._write_checked(f"{loadmode_to_unit(mode)}:RANGe {value}")

    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        self._write_checked(f"CURRent:SLEW:{direction.value} {rate}")

    def output_enable(self, enable: bool, channel: int) -> None:
        self._write_checked(f"INPut {int(enable)}")

    def get_current(self, channel: int) -> float:
        return self._query_checked_float("MEASure:CURRent?")

    def get_voltage(self, channel: int) -> float:
        return self._query_checked_float("MEASure:VOLTage?")

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
        if not err.startswith("0"):
            raise RuntimeError(f"BK85XXB reported error: {err}")
