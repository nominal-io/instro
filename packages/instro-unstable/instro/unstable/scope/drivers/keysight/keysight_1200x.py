"""Keysight InfiniiVision 1200 X-Series oscilloscope driver (EDUX1052G and family)."""

from __future__ import annotations

import math

from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.unstable.scope.driver import ScopeDriverBase
from instro.unstable.scope.types import (
    AcquisitionMode,
    AcquisitionState,
    Coupling,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerStatus,
    TriggerType,
    WaveformData,
)

_ACQ_MODE_TO_SCPI = {
    AcquisitionMode.NORMAL: "NORMal",
    AcquisitionMode.AVERAGE: "AVERage",
    AcquisitionMode.HIGH_RESOLUTION: "HRESolution",
    AcquisitionMode.PEAK_DETECT: "PEAK",
}

_SCPI_TO_ACQ_MODE = {v.upper(): k for k, v in _ACQ_MODE_TO_SCPI.items()}

_COUPLING_TO_SCPI = {
    Coupling.AC: "AC",
    Coupling.DC: "DC",
}

_TRIGGER_TYPE_TO_SCPI = {
    TriggerType.EDGE: "EDGE",
    TriggerType.PULSE: "GLITch",
}

_TRIGGER_SLOPE_TO_SCPI = {
    TriggerSlope.RISING: "POSitive",
    TriggerSlope.FALLING: "NEGative",
    TriggerSlope.EITHER: "EITHer",
}

_TRIGGER_SWEEP_TO_SCPI = {
    TriggerMode.AUTO: "AUTO",
    TriggerMode.NORMAL: "NORMal",
}

_MEAS_INSTALL_MAP = {
    ScopeMeasurementType.VPP: ":MEASure:VPP ",
    ScopeMeasurementType.VMAX: ":MEASure:VMAX ",
    ScopeMeasurementType.VMIN: ":MEASure:VMIN ",
    ScopeMeasurementType.VAVG: ":MEASure:VAVerage DISPlay,",
    ScopeMeasurementType.VRMS: ":MEASure:VRMS DISPlay,DC,",
    ScopeMeasurementType.FREQUENCY: ":MEASure:FREQuency ",
    ScopeMeasurementType.PERIOD: ":MEASure:PERiod ",
    ScopeMeasurementType.DUTY_CYCLE: ":MEASure:DUTYcycle ",
}

_MEAS_QUERY_MAP = {
    ScopeMeasurementType.VPP: ":MEASure:VPP? ",
    ScopeMeasurementType.VMAX: ":MEASure:VMAX? ",
    ScopeMeasurementType.VMIN: ":MEASure:VMIN? ",
    ScopeMeasurementType.VAVG: ":MEASure:VAVerage? DISPlay,",
    ScopeMeasurementType.VRMS: ":MEASure:VRMS? DISPlay,DC,",
    ScopeMeasurementType.FREQUENCY: ":MEASure:FREQuency? ",
    ScopeMeasurementType.PERIOD: ":MEASure:PERiod? ",
    ScopeMeasurementType.DUTY_CYCLE: ":MEASure:DUTYcycle? ",
}

# IEEE-488.2 "not a number" sentinel returned by SCPI scopes when a
# measurement has no valid result (e.g. no waveform, channel off).
_VENDOR_INVALID_MEASUREMENT = 9.91e37


def _convert_sentinel(value: float) -> float:
    """Map the vendor invalid-measurement sentinel to NaN."""
    if abs(value) >= _VENDOR_INVALID_MEASUREMENT:
        return math.nan
    return value


class Keysight1200X(ScopeDriverBase):
    """SCPI driver for Keysight InfiniiVision 1200 X-Series oscilloscopes."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._trigger_source: int | None = None

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def check_errors(self) -> None:
        """Drain ``:SYSTem:ERRor?`` and raise on the first non-zero code."""
        while True:
            resp = self._visa.query(":SYSTem:ERRor?")
            parts = resp.split(",", 1)
            code = int(parts[0])
            if code == 0:
                return
            msg = parts[1].strip().strip('"') if len(parts) > 1 else "Unknown error"
            raise RuntimeError(f"Keysight SCPI error {code}: {msg}")

    # --- Channel vertical settings ---

    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        self._visa.write(f":CHANnel{channel}:SCALe {volts_per_div}")

    def get_vertical_scale(self, channel: int) -> float:
        return float(self._visa.query(f":CHANnel{channel}:SCALe?"))

    def set_vertical_offset(self, offset: float, channel: int) -> None:
        self._visa.write(f":CHANnel{channel}:OFFSet {offset}")

    def get_vertical_offset(self, channel: int) -> float:
        return float(self._visa.query(f":CHANnel{channel}:OFFSet?"))

    def set_coupling(self, coupling: Coupling, channel: int) -> None:
        self._visa.write(f":CHANnel{channel}:COUPling {_COUPLING_TO_SCPI[coupling]}")

    def get_coupling(self, channel: int) -> Coupling:
        resp = self._visa.query(f":CHANnel{channel}:COUPling?").strip().upper()
        if resp == "AC":
            return Coupling.AC
        return Coupling.DC

    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        self._visa.write(f":CHANnel{channel}:PROBe {factor}")

    def get_probe_attenuation(self, channel: int) -> float:
        return float(self._visa.query(f":CHANnel{channel}:PROBe?"))

    # --- Horizontal (timebase) settings ---

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        self._visa.write(f":TIMebase:SCALe {seconds_per_div}")

    def get_horizontal_scale(self) -> float:
        return float(self._visa.query(":TIMebase:SCALe?"))

    # --- Sample rate ---

    def get_sample_rate(self) -> float:
        return float(self._visa.query(":ACQuire:SRATe?"))

    # --- Acquisition ---

    def set_acquisition_mode(self, mode: AcquisitionMode) -> None:
        if mode == AcquisitionMode.ENVELOPE:
            raise NotImplementedError("ENVELOPE acquisition mode is not supported on Keysight 1200X series")
        self._visa.write(f":ACQuire:TYPE {_ACQ_MODE_TO_SCPI[mode]}")

    def get_acquisition_mode(self) -> AcquisitionMode:
        resp = self._visa.query(":ACQuire:TYPE?").strip().upper()
        return _SCPI_TO_ACQ_MODE.get(resp, AcquisitionMode.NORMAL)

    def set_average_count(self, count: int) -> None:
        self._visa.write(f":ACQuire:COUNt {count}")

    def get_average_count(self) -> int:
        return int(float(self._visa.query(":ACQuire:COUNt?")))

    def run(self) -> None:
        self._visa.write(":RUN")

    def stop(self) -> None:
        self._visa.write(":STOP")

    def single(self) -> None:
        self._visa.write(":SINGle")

    def digitize(self, timeout: float) -> None:
        """``:DIGitize`` the cached trigger source, then ``*OPC?`` under a scoped VISA timeout.

        On timeout, ``clear()`` aborts the pending op and restores the session.
        """
        source = self._trigger_source or 1
        self._visa.write(f":DIGitize CHANnel{source}")
        try:
            with self._visa.temporary_timeout(int(timeout * 1000)):
                self._visa.query("*OPC?")
        except Exception as exc:
            self._visa.clear()
            raise TimeoutError(
                f"Acquisition did not complete within {timeout}s. The trigger condition may not have been met."
            ) from exc

    def get_acquisition_state(self) -> AcquisitionState:
        """``:OPERegister:CONDition?`` bit 3 (0x08) — set = RUNNING, clear = STOPPED."""
        resp = int(self._visa.query(":OPERegister:CONDition?"))
        if resp & 0x08:
            return AcquisitionState.RUNNING
        return AcquisitionState.STOPPED

    # --- Waveform data ---

    def fetch_waveform(self, channel: int) -> WaveformData:
        """Fetch the waveform from ``channel`` over ``WORD`` (unsigned 16-bit, LSB-first), 1000 points."""
        self._visa.write(f":WAVeform:SOURce CHANnel{channel}")
        self._visa.write(":WAVeform:FORMat WORD")
        self._visa.write(":WAVeform:BYTeorder LSBFirst")
        self._visa.write(":WAVeform:POINts:MODE NORMal")
        self._visa.write(":WAVeform:POINts 1000")
        # Check errors before querying data — if any setup command failed,
        # the data query would hang waiting for a response that won't come.
        self.check_errors()

        preamble = self._visa.query(":WAVeform:PREamble?")
        parts = preamble.split(",")
        # format, type, points, count, xincrement, xorigin, xreference, yincrement, yorigin, yreference
        nr_pt = int(parts[2])
        x_incr = float(parts[4])  # period time, seconds, float
        x_origin = float(parts[5])
        x_ref = float(parts[6])
        y_incr = float(parts[7])
        y_origin = float(parts[8])
        y_ref = float(parts[9])

        x_incr_ns = int(x_incr * 1e9)  # convert to nanoseconds integer, for greater compatibility with library
        x_ref_ns = int(x_ref * 1e9)
        x_origin_ns = int(x_origin * 1e9)

        points = self._visa.query_binary_values(":WAVeform:DATA?", datatype="H", is_big_endian=False, container=list)

        times = [(i - x_ref_ns) * x_incr_ns + x_origin_ns for i in range(nr_pt)]
        voltages = [(pt - y_ref) * y_incr + y_origin for pt in points]

        return WaveformData(times=times, voltages=voltages)

    # --- Measurements ---

    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        """Install then query a built-in measurement on ``channel``.

        The install form runs first as a guard — bad command syntax surfaces
        via ``check_errors()`` before we'd block on the query. The vendor's
        invalid-measurement sentinel maps to ``NaN``.
        """
        source = f"CHANnel{channel}"
        install_base = _MEAS_INSTALL_MAP[measurement_type]
        self._visa.write(f"{install_base}{source}")
        self.check_errors()
        query_base = _MEAS_QUERY_MAP[measurement_type]
        value = float(self._visa.query(f"{query_base}{source}"))
        return _convert_sentinel(value)

    # --- Trigger ---

    def set_trigger_source(self, channel: int) -> None:
        self._visa.write(f":TRIGger:EDGE:SOURce CHANnel{channel}")
        self._trigger_source = channel

    def set_trigger_type(self, trigger_type: TriggerType) -> None:
        self._visa.write(f":TRIGger:MODE {_TRIGGER_TYPE_TO_SCPI[trigger_type]}")

    def set_trigger_level(self, level: float) -> None:
        source = self._trigger_source
        if source is not None:
            self._visa.write(f":TRIGger:EDGE:LEVel {level},CHANnel{source}")
        else:
            self._visa.write(f":TRIGger:EDGE:LEVel {level}")

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        self._visa.write(f":TRIGger:EDGE:SLOPe {_TRIGGER_SLOPE_TO_SCPI[slope]}")

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        self._visa.write(f":TRIGger:SWEep {_TRIGGER_SWEEP_TO_SCPI[mode]}")

    def force_trigger(self) -> None:
        self._visa.write(":TRIGger:FORCe")

    def get_trigger_status(self) -> TriggerStatus:
        """``:OPERegister:CONDition?`` bits: 0x20 = ARMED, 0x08 = AUTO (running), else TRIGGERED."""
        resp = int(self._visa.query(":OPERegister:CONDition?"))
        if resp & 0x20:
            return TriggerStatus.ARMED
        if resp & 0x08:
            return TriggerStatus.AUTO
        return TriggerStatus.TRIGGERED

    # --- File operations ---

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        """``:SAVE:IMAGe`` to the scope, or ``:DISPlay:DATA? PNG,COLor`` and write locally."""
        if to_instrument:
            self._visa.write(f':SAVE:IMAGe "{filepath}"')
            self.check_errors()
            return b""
        raw_vals = self._visa.query_binary_values(":DISPlay:DATA? PNG,COLor", datatype="B", container=list)
        data = bytes(raw_vals)
        with open(filepath, "wb") as f:
            f.write(data)
        return data

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        """``:SAVE:SETup`` to scope memory or to the host (``:SYSTem:SETup?``)."""
        if to_instrument:
            self._visa.write(f':SAVE:SETup "{name}"')
            self.check_errors()
            return b""
        self._visa.write(":SAVE:SETup 0")
        self.check_errors()
        raw_vals = self._visa.query_binary_values(":SYSTem:SETup?", datatype="B", container=list)
        data = bytes(raw_vals)
        with open(name, "wb") as f:
            f.write(data)
        return data

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        """``:RECall:SETup`` from scope memory, or send local bytes via ``:SYSTem:SETup``."""
        if from_instrument:
            self._visa.write(f':RECall:SETup "{name}"')
            return
        with open(name, "rb") as f:
            data = f.read()
        header = f"#{len(str(len(data)))}{len(data)}"
        self._visa.write_raw(f":SYSTem:SETup {header}".encode() + data)
