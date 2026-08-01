"""
RIGOL DL3031A DC eload driver
"""

from typing import Literal
from instro.eload import ELoadDriverBase
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports.visa import VisaConfig, VisaDriver

TransientCurrMode = Literal["CONT", "CONTinuous", "PULS", "PULSe", "TOGG", "TOGGle"]
MinMaxDef = Literal["MIN", "MINimum", "MAX", "MAXimum", "DEF", "DEFault"]
MinMax = Literal["MIN", "MINimum", "MAX", "MAXimum"]


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
        range: float | MinMaxDef | None = None,
        v_on: float | MinMaxDef | None = None,
        v_on_delay: float | MinMaxDef | None = None,
        i_set: float | MinMaxDef | None = None,
        i_step: float | MinMaxDef | None = None,
        i_delay_step: float | MinMaxDef | None = None,
        i_max: float | MinMaxDef | None = None,
        i_min: float | MinMaxDef | None = None,
        v_ocp: float | MinMaxDef | None = None,
        t_ocp: float | MinMaxDef | None = None,
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
        v_on: float | MinMaxDef | None = None,
        v_on_delay: float | MinMaxDef | None = None,
        p_set: float | MinMaxDef | None = None,
        p_step: float | MinMaxDef | None = None,
        p_delay_step: float | MinMaxDef | None = None,
        p_max: float | MinMaxDef | None = None,
        p_min: float | MinMaxDef | None = None,
        v_opp: float | MinMaxDef | None = None,
        t_opp: float | MinMaxDef | None = None,
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

    def set_transient_curr_params(
        self,
        mode: TransientCurrMode | None = None,
        a_level: float | MinMaxDef | None = None,
        b_level: float | MinMaxDef | None = None,
        a_width: float | MinMaxDef | None = None,
        b_width: float | MinMaxDef | None = None,
        freq: float | MinMaxDef | None = None,
        period: float | MinMaxDef | None = None,
        a_duty: float | MinMaxDef | None = None,
    ) -> None:
        """
        Configure transient operation in CC mode.
        """
        scpi_commands_to_params = {
            "MODE": mode,
            "ALEVel": a_level,
            "BLEVel": b_level,
            "AWIDth": a_width,
            "BWIDth": b_width,
            "FREQuency": freq,
            "PERiod": period,
            "ADUTy": a_duty,
        }
        # only write user-provided fields
        for command, value in scpi_commands_to_params.items():
            if value is not None:
                self._write_checked(f"CURRent:TRANsient:{command} {value}")

    def set_cc_params(
        self,
        range: float | MinMaxDef | None = None,
        v_on: float | MinMaxDef | None = None,
        v_limit: float | MinMaxDef | None = None,
        i_limit: float | MinMaxDef | None = None,
    ) -> None:
        """
        Configure CC mode (except slew).
        """
        scpi_commands_to_params = {"RANGe": range, "VON": v_on, "VLIMt": v_limit, "ILIMt": i_limit}
        # only write user-provided fields
        for command, value in scpi_commands_to_params.items():
            if value is not None:
                self._write_checked(f"CURRent:{command} {value}")

    def set_cv_params(
        self,
        range: float | MinMaxDef | None = None,
        v_limit: float | MinMaxDef | None = None,
        i_limit: float | MinMaxDef | None = None,
    ) -> None:
        """
        Configure CV mode.
        """
        scpi_commands_to_params = {"RANGe": range, "VLIMt": v_limit, "ILIMt": i_limit}
        # only write user-provided fields
        for command, value in scpi_commands_to_params.items():
            if value is not None:
                self._write_checked(f"VOLTage:{command} {value}")

    def set_cr_params(
        self,
        range: float | MinMaxDef | None = None,
        v_limit: float | MinMaxDef | None = None,
        i_limit: float | MinMaxDef | None = None,
    ) -> None:
        """
        Configure CR mode.
        """
        scpi_commands_to_params = {"RANGe": range, "VLIMt": v_limit, "ILIMt": i_limit}
        # only write user-provided fields
        for command, value in scpi_commands_to_params.items():
            if value is not None:
                self._write_checked(f"RESistance:{command} {value}")

    def set_cp_params(self, v_limit: float | MinMaxDef | None = None, i_limit: float | MinMaxDef | None = None) -> None:
        """
        Configure CP mode.
        """
        scpi_commands_to_params = {"VLIMt": v_limit, "ILIMt": i_limit}
        # only write user-provided fields
        for command, value in scpi_commands_to_params.items():
            if value is not None:
                self._write_checked(f"POWer:{command} {value}")

    def set_list_params(
        self,
        mode: LoadMode | None = None,
        range: float | None = None,
        count: int | MinMax | None = None,
        step: int | MinMax | None = None,
        end_state: Literal["LAST", "OFF"] | None = None,
    ) -> None:
        """
        Configure list mode.
        """
        scpi_commands_to_params = {
            "MODE": mode.value if mode is not None else None,
            "RANGe": range,
            "COUNt": count,
            "STEP": step,
            "END": end_state,
        }
        # only write user-provided fields
        for command, value in scpi_commands_to_params.items():
            if value is not None:
                self._write_checked(f"LIST:{command} {value}")

    def set_list_step_params(
        self,
        step_num: int,
        level: float | None = None,
        width: float | None = None,
        slew: float | None = None,
    ) -> None:
        """
        Configure individual step within list mode.
        """
        scpi_commands_to_params = {"LEVel": level, "WIDth": width, "SLEW": slew}
        for command, value in scpi_commands_to_params.items():
            if value is not None:
                self._write_checked(f"LIST:{command} {step_num},{value}")

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
