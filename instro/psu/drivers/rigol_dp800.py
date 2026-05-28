"""Rigol DP800-series PSU driver. Covers DP811, DP821, DP831, DP832."""

from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase


class RigolDP800(PSUDriverBase):
    """Rigol DP800-series multi-channel PSU (DP811/DP821/DP831/DP832)."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self.idn = ""

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int = 1) -> None:
        self._write_checked(f":SOUR{channel}:VOLT {voltage:.3f}")

    def get_voltage(self, channel: int = 1) -> float:
        return self._query_checked_float(f":MEAS:VOLT? CH{channel}")

    def set_current_limit(self, current_limit: float, channel: int = 1) -> None:
        self._write_checked(f":SOUR{channel}:CURR {current_limit:.3f}")

    def get_current(self, channel: int = 1) -> float:
        return self._query_checked_float(f":MEAS:CURR? CH{channel}")

    def output_enable(self, enable: bool, channel: int = 1) -> None:
        cmd = f":OUTP CH{channel},ON" if enable else f":OUTP CH{channel},OFF"
        self._write_checked(cmd)

    def get_output_status(self, channel: int = 1) -> bool:
        with self._visa.lock():
            resp = self._visa.query(f":OUTP? CH{channel}")
            self._check_errors()
        return resp == "ON"

    def query_status(self) -> dict:
        """Query the status of the PSU (output enable, regulation mode, OVP/OCP flags)."""
        status: dict = {}

        with self._visa.lock():
            if not self.idn:
                self.idn = self._visa.query("*IDN?")
                self._check_errors()

            if "DP832" in self.idn or "DP831" in self.idn:
                num_channels = 3
            elif "DP821" in self.idn:
                num_channels = 2
            elif "DP811" in self.idn:
                num_channels = 1
            else:
                raise RuntimeError(f"Unrecognized Rigol PSU model: {self.idn}")

            for channel in range(1, num_channels + 1):
                channel_dict: dict = {}
                channel_dict["enable"] = self.get_output_status(channel)

                cond_code = int(self._visa.query(f":STAT:QUES:INST:ISUM{channel}:COND?"))
                self._check_errors()
                channel_dict.update(self._decode_channel_condition(cond_code))

                status[f"ch{channel}"] = channel_dict

        return status

    def _decode_channel_condition(self, cond_code: int) -> dict:
        """Decode questionable instrument summary condition bits for a given channel."""
        match cond_code & 3:
            case 0:
                mode = "off"
            case 1:
                mode = "CC"
            case 2:
                mode = "CV"
            case 3:
                mode = "UNREGULATED"
            case _:
                mode = "UNDEFINED"

        return {
            "mode": mode,
            "OVP": bool(cond_code & 4),
            "OCP": bool(cond_code & 8),
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
        err = self._visa.query(":SYST:ERR?")
        if not err.startswith("0"):
            raise RuntimeError(f"Rigol PSU reported error: {err}")
