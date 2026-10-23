"""Keysight EL30000-series E-Load driver. Covers EL33133A/EL34143A (1 channel) and EL34243A (2 channels).

Written from the EL30000 Programming Guide; hardware-validated against an EL34243A.
"""

from instro.eload import ELoadDriverBase
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports.visa import VisaConfig, VisaDriver

_A_PER_US_TO_A_PER_S = 1e6


def loadmode_to_function(mode: LoadMode) -> str:
    return {
        LoadMode.CC: "CURRent",
        LoadMode.CV: "VOLTage",
        LoadMode.CP: "POWer",
        LoadMode.CR: "RESistance",
    }[mode]


class KeysightEL30000(ELoadDriverBase):
    """Keysight EL30000-series E-Load (EL33133A/EL34143A/EL34243A)."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        """Initialize the driver; the connection opens when ``InstroELoad`` opens.

        Args:
            visa_resource (str | VisaConfig): VISA resource string, or a `VisaConfig` for custom connection settings.

        Example::

            from instro.eload import InstroELoad
            from instro.eload.drivers import KeysightEL30000
            from instro.eload.types import LoadMode

            eload = InstroELoad(
                name="load",
                driver=KeysightEL30000("USB0::0x2A8D::0x3902::MY12345678::INSTR"),
            )
            eload.open()
            eload.set_mode(LoadMode.CC, channel=2)
            eload.set_level(0.2, channel=2)
            eload.output_enable(True, channel=2)
        """
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()
        try:
            self._visa.write("*CLS")  # drop errors queued by earlier sessions so they aren't blamed on this one
            self._write_checked("SYST:REM")
        except Exception:
            self._visa.close()
            raise

    def close(self) -> None:
        self._visa.close()

    def short_output(self, enable: bool, channel: int) -> None:
        # INP:SHOR takes no channel list, so it acts on the selected channel.
        with self._visa.lock():
            self._write_checked(f"INST:NSEL {channel}")
            self._write_checked(f"INP:SHOR {int(enable)}")

    def set_mode(self, mode: LoadMode, channel: int) -> None:
        self._write_checked(f"FUNC {loadmode_to_function(mode)}, (@{channel})")

    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        with self._visa.lock():
            if mode is LoadMode.CV and curr_limit is not None:
                self._write_checked(f"CURR:LIM {curr_limit}, (@{channel})")
            self._write_checked(f"{loadmode_to_function(mode)} {value}, (@{channel})")

    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        try:
            self._write_checked(f"{loadmode_to_function(mode)}:RANG {value}, (@{channel})")
        except RuntimeError as exc:
            if "-222" not in str(exc):
                raise
            raise RuntimeError(
                f"KeysightEL30000 rejected {mode.value} range {value}: the instrument refuses a range that does not "
                f"contain channel {channel}'s present {mode.value} level. Set a level inside the new range first."
            ) from exc

    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        rate_a_per_s = rate * _A_PER_US_TO_A_PER_S
        with self._visa.lock():
            if direction in (SlewRateDirection.RISE, SlewRateDirection.BOTH):
                self._write_checked(f"CURR:SLEW {rate_a_per_s}, (@{channel})")
            if direction in (SlewRateDirection.FALL, SlewRateDirection.BOTH):
                self._write_checked(f"CURR:SLEW:NEG {rate_a_per_s}, (@{channel})")

    def output_enable(self, enable: bool, channel: int) -> None:
        self._write_checked(f"INP {int(enable)}, (@{channel})")

    def get_current(self, channel: int) -> float:
        return self._query_checked_float(f"MEAS:CURR? (@{channel})")

    def get_voltage(self, channel: int) -> float:
        return self._query_checked_float(f"MEAS:VOLT? (@{channel})")

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
            raise RuntimeError(f"KeysightEL30000 reported error: {err}")
