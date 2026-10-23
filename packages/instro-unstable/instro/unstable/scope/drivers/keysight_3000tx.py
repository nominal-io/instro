"""Keysight InfiniiVision 3000T X-Series oscilloscope driver (DSOX3xxxT / MSOX3xxxT)."""

from __future__ import annotations

import math

import pyvisa.constants
import pyvisa.errors

from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.scope import ScopeDriverBase
from instro.scope.types import (
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

# The scope answers :ACQuire:TYPE? with the 4-character short form (NORM, AVER, HRES, PEAK).
_SCPI_TO_ACQ_MODE = {v[:4].upper(): k for k, v in _ACQ_MODE_TO_SCPI.items()}

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

# The programmer's guide documents +9.9E+37 as the "measurement cannot be made"
# value; the IEEE-488.2 NaN sentinel 9.91E+37 is also caught by the >= compare.
_VENDOR_INVALID_MEASUREMENT = 9.9e37

_WAVEFORM_POINTS = 1000

# :SYSTem:ERRor? queue depth; bounds the drain loop if the scope keeps reporting errors.
_ERROR_QUEUE_DEPTH = 30


def _convert_sentinel(value: float) -> float:
    """Map the vendor invalid-measurement sentinel to NaN."""
    if abs(value) >= _VENDOR_INVALID_MEASUREMENT:
        return math.nan
    return value


def _is_visa_timeout(exc: BaseException) -> bool:
    """True for a VISA read timeout (``VI_ERROR_TMO``) or a plain ``TimeoutError``."""
    if isinstance(exc, pyvisa.errors.VisaIOError):
        return exc.error_code == pyvisa.constants.StatusCode.error_timeout
    return isinstance(exc, TimeoutError)


class Keysight3000TX(ScopeDriverBase):
    """SCPI driver for Keysight InfiniiVision 3000T X-Series oscilloscopes (2- and 4-channel DSO/MSO)."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._trigger_source: int | None = None

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def check_errors(self) -> None:
        """Drain ``:SYSTem:ERRor?`` until it reports ``0`` and raise listing every non-zero entry."""
        errors: list[str] = []
        for _ in range(_ERROR_QUEUE_DEPTH):
            parts = self._visa.query(":SYSTem:ERRor?").split(",", 1)
            code = int(parts[0])
            if code == 0:
                break
            msg = parts[1].strip().strip('"') if len(parts) > 1 else "Unknown error"
            errors.append(f"{code}: {msg}")
        if errors:
            raise RuntimeError(f"Keysight SCPI error {'; '.join(errors)}")

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

    def set_channel_display(self, enabled: bool, channel: int) -> None:
        """Turn ``channel`` on or off. Waveform fetches and measurements need the channel displayed."""
        self._visa.write(f":CHANnel{channel}:DISPlay {1 if enabled else 0}")

    def get_channel_display(self, channel: int) -> bool:
        return self._visa.query(f":CHANnel{channel}:DISPlay?").strip() == "1"

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
            raise NotImplementedError("ENVELOPE acquisition mode is not supported on Keysight 3000T X-Series")
        self._visa.write(f":ACQuire:TYPE {_ACQ_MODE_TO_SCPI[mode]}")

    def get_acquisition_mode(self) -> AcquisitionMode:
        resp = self._visa.query(":ACQuire:TYPE?").strip().upper()
        return _SCPI_TO_ACQ_MODE.get(resp[:4], AcquisitionMode.NORMAL)

    def set_average_count(self, count: int) -> None:
        self._visa.write(f":ACQuire:COUNt {count}")

    def get_average_count(self) -> int:
        return int(float(self._visa.query(":ACQuire:COUNt?")))

    def run(self) -> None:
        self._visa.write(":RUN")

    def stop(self) -> None:
        self._visa.write(":STOP")

    def single(self) -> None:
        """``:SINGle``. On the X-Series this also switches ``:TRIGger:SWEep`` to NORMal."""
        self._visa.write(":SINGle")

    def digitize(self, timeout: float) -> None:
        """``:DIGitize`` the cached trigger source, then ``*OPC?`` under a scoped VISA timeout.

        On any failure ``clear()`` aborts the pending op so the session stays usable;
        only a VISA timeout is reported as ``TimeoutError``, other errors propagate as-is.
        """
        source = self._trigger_source or 1
        self._visa.write(f":DIGitize CHANnel{source}")
        try:
            with self._visa.temporary_timeout(int(timeout * 1000)):
                self._visa.query("*OPC?")
        except Exception as exc:
            self._visa.clear()
            if not _is_visa_timeout(exc):
                raise
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
        """Fetch the waveform from ``channel`` over ``WORD`` (unsigned 16-bit, LSB-first), requesting 1000 points.

        The scope returns as close to 1000 points as its settings allow (it may return
        fewer, e.g. 992); the transfer is checked against the preamble's point count.
        """
        self._visa.write(f":WAVeform:SOURce CHANnel{channel}")
        self._visa.write(":WAVeform:FORMat WORD")
        self._visa.write(":WAVeform:UNSigned 1")
        self._visa.write(":WAVeform:BYTeorder LSBFirst")
        self._visa.write(":WAVeform:POINts:MODE NORMal")
        self._visa.write(f":WAVeform:POINts {_WAVEFORM_POINTS}")
        # Check errors before querying data — if any setup command failed,
        # the data query would hang waiting for a response that won't come.
        self.check_errors()

        preamble = self._visa.query(":WAVeform:PREamble?")
        parts = preamble.split(",")
        # format, type, points, count, xincrement, xorigin, xreference, yincrement, yorigin, yreference
        nr_pt = int(parts[2])
        x_incr = float(parts[4])
        x_origin = float(parts[5])
        x_ref = float(parts[6])
        y_incr = float(parts[7])
        y_origin = float(parts[8])
        y_ref = float(parts[9])

        points = self._visa.query_binary_values(":WAVeform:DATA?", datatype="H", is_big_endian=False, container=list)
        if len(points) != nr_pt:
            raise RuntimeError(f"Waveform transfer returned {len(points)} points but the preamble declared {nr_pt}")

        # Scale in seconds and round each finished timestamp to integer ns; at
        # 5 GSa/s the 0.2 ns interval would otherwise truncate to zero.
        times = [round(((i - x_ref) * x_incr + x_origin) * 1e9) for i in range(nr_pt)]
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
        """``:TRIGger:EDGE:LEVel``; a ``<source>`` is only honored for the active trigger source, so the cached one is used."""
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
        """``:OPERegister:CONDition?`` bits: 0x20 = ARMED, 0x08 = AUTO (running); a stopped scope reports TRIGGERED."""
        resp = int(self._visa.query(":OPERegister:CONDition?"))
        if resp & 0x20:
            return TriggerStatus.ARMED
        if resp & 0x08:
            return TriggerStatus.AUTO
        return TriggerStatus.TRIGGERED

    # --- File operations ---

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        """``:SAVE:IMAGe`` (as PNG) to the scope, or ``:DISPlay:DATA? PNG,COLor`` and write locally."""
        if to_instrument:
            # :SAVE:IMAGe fails when the front panel has a waveform-data format selected.
            self._visa.write(":SAVE:IMAGe:FORMat PNG")
            self._visa.write(f':SAVE:IMAGe "{filepath}"')
            self.check_errors()
            return b""
        # Ink saver is on by default and inverts the returned image; restore the user's setting after the capture.
        ink_saver = self._visa.query(":HARDcopy:INKSaver?").strip()
        self._visa.write(":HARDcopy:INKSaver OFF")
        try:
            raw_vals = self._visa.query_binary_values(":DISPlay:DATA? PNG,COLor", datatype="B", container=list)
        finally:
            self._visa.write(f":HARDcopy:INKSaver {ink_saver}")
        data = bytes(raw_vals)
        with open(filepath, "wb") as f:
            f.write(data)
        return data

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        """``:SAVE:SETup "<name>"`` to the scope's file system (``.scp``), or ``:SYSTem:SETup?`` to the host."""
        if to_instrument:
            self._visa.write(f':SAVE:SETup "{name}"')
            self.check_errors()
            return b""
        raw_vals = self._visa.query_binary_values(":SYSTem:SETup?", datatype="B", container=list)
        data = bytes(raw_vals)
        with open(name, "wb") as f:
            f.write(data)
        return data

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        """``:RECall:SETup`` from scope memory, or send local bytes via ``:SYSTem:SETup``.

        The recalled setup may select a different trigger source, so the cached
        one is dropped; call ``set_trigger_source`` again before ``set_trigger_level``.
        """
        if from_instrument:
            self._visa.write(f':RECall:SETup "{name}"')
        else:
            with open(name, "rb") as f:
                data = f.read()
            header = f"#{len(str(len(data)))}{len(data)}"
            self._visa.write_raw(f":SYSTem:SETup {header}".encode() + data)
        self._trigger_source = None
