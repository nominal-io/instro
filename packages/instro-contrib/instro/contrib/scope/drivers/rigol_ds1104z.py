"""Rigol DS1104Z oscilloscope driver (DS1000Z series)."""

from __future__ import annotations

import math
import time

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
    AcquisitionMode.AVERAGE: "AVERages",
    AcquisitionMode.HIGH_RESOLUTION: "HRESolution",
    AcquisitionMode.PEAK_DETECT: "PEAK",
}

_SCPI_TO_ACQ_MODE = {
    "NORM": AcquisitionMode.NORMAL,
    "AVER": AcquisitionMode.AVERAGE,
    "HRES": AcquisitionMode.HIGH_RESOLUTION,
    "PEAK": AcquisitionMode.PEAK_DETECT,
}

_TRIGGER_TYPE_TO_SCPI = {
    TriggerType.EDGE: "EDGE",
    TriggerType.PULSE: "PULSe",
}

_TRIGGER_SLOPE_TO_SCPI = {
    TriggerSlope.RISING: "POSitive",
    TriggerSlope.FALLING: "NEGative",
    TriggerSlope.EITHER: "RFALl",
}

_TRIGGER_MODE_TO_SCPI = {
    TriggerMode.AUTO: "AUTO",
    TriggerMode.NORMAL: "NORMal",
}

# DS1104Z has no single generic duty-cycle item, only positive/negative variants; PDUTy matches
# what most other vendors call "duty cycle".
_MEAS_TYPE_TO_SCPI = {
    ScopeMeasurementType.VPP: "VPP",
    ScopeMeasurementType.VMAX: "VMAX",
    ScopeMeasurementType.VMIN: "VMIN",
    ScopeMeasurementType.VAVG: "VAVG",
    ScopeMeasurementType.VRMS: "VRMS",
    ScopeMeasurementType.FREQUENCY: "FREQuency",
    ScopeMeasurementType.PERIOD: "PERiod",
    ScopeMeasurementType.DUTY_CYCLE: "PDUTy",
}

# :TRIGger:STATus? reply strings aren't individually explained in the programming guide;
# this mapping is inferred and should be confirmed against real hardware.
_TRIGGER_STATUS_MAP = {
    "TD": TriggerStatus.TRIGGERED,
    "WAIT": TriggerStatus.ARMED,
    "RUN": TriggerStatus.READY,
    "AUTO": TriggerStatus.AUTO,
    "STOP": TriggerStatus.READY,
}

# Documented only for :CURSor:AUTO:*Value? in the manual; assumed to also apply to
# :MEASure:ITEM? by RIGOL's convention elsewhere, but unconfirmed for this exact query.
_VENDOR_INVALID_MEASUREMENT = 9.9e37


class RigolDS1104Z(ScopeDriverBase):
    """SCPI driver for the Rigol DS1104Z oscilloscope (DS1000Z series)."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._trigger_type: TriggerType = TriggerType.EDGE
        self._trigger_source: int = 1

    def open(self) -> None:
        self._visa.open()
        self._visa.write("*CLS")

    def close(self) -> None:
        self._visa.close()

    def _consume_trailing_terminator(self) -> None:
        """Drain the stray LF the DS1104Z appends after a binary-block reply; it would otherwise desync the next query."""
        try:
            with self._visa.temporary_timeout(400):
                self._visa.read_raw()
        except Exception:  # noqa: BLE001 - nothing buffered is the normal case
            pass

    def check_errors(self) -> None:
        """Poll ``:SYSTem:ERRor?`` and raise on a non-zero code.

        The manual documents the ``<code>,"<message>"`` reply shape but never shows the exact
        no-error string; ``0`` is RIGOL's convention elsewhere and is assumed here pending
        hardware verification.
        """
        resp = self._visa.query(":SYSTem:ERRor?").strip()
        code = int(resp.split(",")[0])
        if code != 0:
            raise RuntimeError(f"Rigol DS1104Z reported error {code}: {resp}")

    # --- Channel vertical settings ---

    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        self._visa.write(f":CHANnel{channel}:SCALe {volts_per_div:.6E}")

    def get_vertical_scale(self, channel: int) -> float:
        return float(self._visa.query(f":CHANnel{channel}:SCALe?"))

    def set_vertical_offset(self, offset: float, channel: int) -> None:
        self._visa.write(f":CHANnel{channel}:OFFSet {offset:.6E}")

    def get_vertical_offset(self, channel: int) -> float:
        return float(self._visa.query(f":CHANnel{channel}:OFFSet?"))

    def set_coupling(self, coupling: Coupling, channel: int) -> None:
        self._visa.write(f":CHANnel{channel}:COUPling {coupling.value}")

    def get_coupling(self, channel: int) -> Coupling:
        resp = self._visa.query(f":CHANnel{channel}:COUPling?").strip().upper()
        return Coupling.AC if resp == "AC" else Coupling.DC

    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        self._visa.write(f":CHANnel{channel}:PROBe {factor:g}")

    def get_probe_attenuation(self, channel: int) -> float:
        return float(self._visa.query(f":CHANnel{channel}:PROBe?"))

    # --- Horizontal (timebase) settings ---

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        self._visa.write(f":TIMebase:MAIN:SCALe {seconds_per_div:.6E}")

    def get_horizontal_scale(self) -> float:
        return float(self._visa.query(":TIMebase:MAIN:SCALe?"))

    # --- Sample rate ---

    def get_sample_rate(self) -> float:
        return float(self._visa.query(":ACQuire:SRATe?"))

    # --- Acquisition ---

    def set_acquisition_mode(self, mode: AcquisitionMode) -> None:
        if mode == AcquisitionMode.ENVELOPE:
            raise NotImplementedError("ENVELOPE acquisition mode is not supported on the Rigol DS1104Z")
        self._visa.write(f":ACQuire:TYPE {_ACQ_MODE_TO_SCPI[mode]}")

    def get_acquisition_mode(self) -> AcquisitionMode:
        resp = self._visa.query(":ACQuire:TYPE?").strip().upper()
        return _SCPI_TO_ACQ_MODE.get(resp, AcquisitionMode.NORMAL)

    def set_average_count(self, count: int) -> None:
        self._visa.write(f":ACQuire:AVERages {count}")

    def get_average_count(self) -> int:
        return int(self._visa.query(":ACQuire:AVERages?"))

    def run(self) -> None:
        self._visa.write(":RUN")

    def stop(self) -> None:
        self._visa.write(":STOP")

    def single(self) -> None:
        self._visa.write(":SINGle")

    def digitize(self, timeout: float) -> None:
        """Arm a single acquisition then poll ``:TRIGger:STATus?`` until ``STOP`` or ``timeout``.

        The manual never states which status value marks acquisition-complete; ``STOP`` is
        inferred from single-trigger semantics and should be confirmed against real hardware.
        """
        self._visa.write(":SINGle")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._visa.query(":TRIGger:STATus?").strip().upper() == "STOP":
                return
            time.sleep(0.05)
        self._visa.write(":STOP")
        raise TimeoutError(
            f"Acquisition did not complete within {timeout}s. The trigger condition may not have been met."
        )

    def get_acquisition_state(self) -> AcquisitionState:
        resp = self._visa.query(":TRIGger:STATus?").strip().upper()
        return AcquisitionState.STOPPED if resp == "STOP" else AcquisitionState.RUNNING

    # --- Waveform data ---

    def fetch_waveform(self, channel: int) -> WaveformData:
        """Fetch the on-screen waveform (``:WAVeform:MODE NORMal``, max 1200 points) from ``channel``."""
        with self._visa.lock():
            self._visa.write(f":WAVeform:SOURce CHANnel{channel}")
            self._visa.write(":WAVeform:MODE NORMal")
            self._visa.write(":WAVeform:FORMat BYTE")
            preamble = self._visa.query(":WAVeform:PREamble?").strip().split(",")
            x_increment, x_origin = float(preamble[4]), float(preamble[5])
            y_increment, y_origin, y_reference = float(preamble[7]), float(preamble[8]), float(preamble[9])
            codes = self._visa.query_binary_values(":WAVeform:DATA?", datatype="B", container=list)
            self._consume_trailing_terminator()

        x_origin_ns = int(x_origin * 1e9)
        x_increment_ns = int(x_increment * 1e9)
        times = [x_origin_ns + i * x_increment_ns for i in range(len(codes))]
        voltages = [(code - y_origin - y_reference) * y_increment for code in codes]

        return WaveformData(times=times, voltages=voltages)

    # --- Measurements ---

    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        item = _MEAS_TYPE_TO_SCPI[measurement_type]
        resp = self._visa.query(f":MEASure:ITEM? {item},CHANnel{channel}")
        try:
            value = float(resp)
        except ValueError:
            return math.nan
        if abs(value) >= _VENDOR_INVALID_MEASUREMENT:
            return math.nan
        return value

    # --- Trigger ---

    def _write_trigger_source(self) -> None:
        subsystem = "EDGe" if self._trigger_type == TriggerType.EDGE else "PULSe"
        self._visa.write(f":TRIGger:{subsystem}:SOURce CHANnel{self._trigger_source}")

    def set_trigger_source(self, channel: int) -> None:
        self._trigger_source = channel
        self._write_trigger_source()

    def set_trigger_type(self, trigger_type: TriggerType) -> None:
        self._trigger_type = trigger_type
        self._visa.write(f":TRIGger:MODE {_TRIGGER_TYPE_TO_SCPI[trigger_type]}")
        self._write_trigger_source()

    def set_trigger_level(self, level: float) -> None:
        subsystem = "EDGe" if self._trigger_type == TriggerType.EDGE else "PULSe"
        self._visa.write(f":TRIGger:{subsystem}:LEVel {level:.6E}")

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        # Slope only exists under the EDGE trigger subsystem; PULSE trigger has no slope concept.
        self._visa.write(f":TRIGger:EDGe:SLOPe {_TRIGGER_SLOPE_TO_SCPI[slope]}")

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        self._visa.write(f":TRIGger:SWEep {_TRIGGER_MODE_TO_SCPI[mode]}")

    def force_trigger(self) -> None:
        self._visa.write(":TFORce")

    def get_trigger_status(self) -> TriggerStatus:
        resp = self._visa.query(":TRIGger:STATus?").strip().upper()
        return _TRIGGER_STATUS_MAP.get(resp, TriggerStatus.ARMED)

    # --- File operations ---

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        """Transfer a screen dump (``:DISPlay:DATA?``, BMP24) to the host; not exposed for on-instrument storage."""
        if to_instrument:
            raise NotImplementedError("DS1104Z screenshot save to instrument storage is not exposed over SCPI")
        with self._visa.lock():
            codes = self._visa.query_binary_values(":DISPlay:DATA?", datatype="B", container=list)
            self._consume_trailing_terminator()
        data = bytes(codes)
        with open(filepath, "wb") as f:
            f.write(data)
        return data

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        """Export the full setup as an opaque binary blob (``:SYSTem:SETup?``); not exposed for on-instrument storage."""
        if to_instrument:
            raise NotImplementedError("DS1104Z settings save to instrument storage is not exposed over SCPI")
        with self._visa.lock():
            codes = self._visa.query_binary_values(":SYSTem:SETup?", datatype="B", container=list)
            self._consume_trailing_terminator()
        data = bytes(codes)
        with open(name, "wb") as f:
            f.write(data)
        return data

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        """Import an opaque setup blob previously produced by :meth:`save_settings`; must round-trip byte-for-byte."""
        if from_instrument:
            raise NotImplementedError("DS1104Z settings load from instrument storage is not exposed over SCPI")
        with open(name, "rb") as f:
            data = f.read()
        header = f"#9{len(data):09d}".encode()
        self._visa.write_raw(b":SYSTem:SETup " + header + data + b"\r\n")
