"""
RIGOL DL3031A DC eload driver
"""

from instro.eload import ELoadDriverBase
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports.visa import VisaConfig, VisaDriver


def loadmode_to_rigol(mode: LoadMode) -> str:
    return {
        LoadMode.CC: "CURRent",
        LoadMode.CV: "VOLTage",
        LoadMode.CP: "POWer",
        LoadMode.CR: "RESistance",
    }[mode]


def slew_direction_to_rigol(direction: SlewRateDirection) -> str:
    return {
        SlewRateDirection.RISE: "POSitive",
        SlewRateDirection.FALL: "NEGative",
        SlewRateDirection.BOTH: "BOTH",
    }[direction]


class RigolDL3031A(ELoadDriverBase):
    """
    TODO: hardware validate base functionality
    """

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        # track short status since not tracked in questionable status register
        self._short_enabled = False  # TODO: verify default state

    def open(self) -> None:
        self._visa.open()
        # no explicit remote enable command
        # TODO: verify auto-enter remote mode upon receiving any SCPI command

    def close(self) -> None:
        self._visa.close()

    def short_output(self, enable: bool, channel: int) -> None:
        # TODO: verify hold vs. toggle functionality of short button
        if enable != self._short_enabled:
            self._write_checked("SYSTem:KEY 33")
            self._short_enabled = enable

    def set_mode(self, mode: LoadMode, channel: int) -> None:
        self._write_checked(f"FUNC {loadmode_to_rigol(mode)}")

    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        self._write_checked(f"{loadmode_to_rigol(mode)}:LEVel:IMMediate {value}")
        if mode is LoadMode.CV and curr_limit is not None:
            self._write_checked(f"VOLTage:ILIMt {curr_limit}")

    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        if mode == LoadMode.CP:
            raise NotImplementedError("Rigol DL3031A has no :RANGe command in CP mode")
        else:
            self._write_checked(f"{loadmode_to_rigol(mode)}:RANGe {value}")

    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        self._write_checked(f"CURRent:SLEW:{slew_direction_to_rigol(direction)} {rate}")

    def output_enable(self, enable: bool, channel: int) -> None:
        self._write_checked(f"INPut {int(enable)}")

    def get_current(self, channel: int) -> float:
        return self._query_checked_float("MEASure:CURRent?")

    def get_voltage(self, channel: int) -> float:
        return self._query_checked_float("MEASure:VOLTage?")

    # following functions are DL3031A-specific extensions beyond ELoadDriverBase class
    def set_ocp_params(
        self,
        range: float | None = None,
        v_on: float | None = None,
        v_on_delay: float | None = None,
        i_set: float | None = None,
        i_step: float | None = None,
        i_delay_step: float | None = None,
        i_max: float | None = None,
        i_min: float | None = None,
        v_ocp: float | None = None,
        t_ocp: float | None = None
    ) -> None:
        """
        Write provided parameters to configure OCP test.
        """
        scpi_commands_to_params = {
            "RANGe": range,
            "VON": v_on,
            "VONDelay": v_on_delay,
            "ISET": i_set,
            "ISTEP": i_step,
            "IDELaystep": i_delay_step,
            "IMAX": i_max,
            "IMIN": i_min,
            "VOCP": v_ocp,
            "TOCP": t_ocp,
        }
        # only write user-provided fields
        for command, value in scpi_commands_to_params.items():
            if value is not None:
                self._write_checked(f"OCP:{command} {value}")

    def set_opp_params(
        self,
        v_on: float | None = None,
        v_on_delay: float | None = None,
        p_set: float | None = None,
        p_step: float | None = None,
        p_delay_step: float | None = None,
        p_max: float | None = None,
        p_min: float | None = None,
        v_opp: float | None = None,
        t_opp: float | None = None
    ) -> None:
        """
        Write provided parameters to configure OPP test.
        """
        scpi_commands_to_params = {
            "VON": v_on,
            "VONDelay": v_on_delay,
            "PSET": p_set,
            "PSTEP": p_step,
            "PDELaystep": p_delay_step,
            "PMAX": p_max,
            "PMIN": p_min,
            "VOPP": v_opp,
            "TOPP": t_opp,
        }
        # only write user-provided fields
        for command, value in scpi_commands_to_params.items():
            if value is not None:
                self._write_checked(f"OPP:{command} {value}")

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
            raise RuntimeError(f"Rigol DL3031A reported error: {err}")
