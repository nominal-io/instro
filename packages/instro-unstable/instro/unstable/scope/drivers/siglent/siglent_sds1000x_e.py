"""Siglent SDS1000X-E series oscilloscope driver (SDS1104X-E and family)."""

from __future__ import annotations

import math
import time

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
    AcquisitionMode.NORMAL: "SAMPLING",
    AcquisitionMode.AVERAGE: "AVERAGE",
    AcquisitionMode.HIGH_RESOLUTION: "HIGH_RES",
    AcquisitionMode.PEAK_DETECT: "PEAK_DETECT",
}

_SCPI_TO_ACQ_MODE = {v.upper(): k for k, v in _ACQ_MODE_TO_SCPI.items()}

# Siglent combines coupling and input impedance in one token; default to 1 MΩ.
_COUPLING_TO_SCPI = {
    Coupling.AC: "A1M",
    Coupling.DC: "D1M",
}

_TRIGGER_TYPE_TO_SCPI = {
    TriggerType.EDGE: "EDGE",
    TriggerType.PULSE: "GLIT",
}

_TRIGGER_SLOPE_TO_SCPI = {
    TriggerSlope.RISING: "POS",
    TriggerSlope.FALLING: "NEG",
    TriggerSlope.EITHER: "WINDOW",
}

_TRIGGER_MODE_TO_SCPI = {
    TriggerMode.AUTO: "AUTO",
    TriggerMode.NORMAL: "NORM",
}

_MEAS_TYPE_TO_SCPI = {
    ScopeMeasurementType.VPP: "PKPK",
    ScopeMeasurementType.VMAX: "MAX",
    ScopeMeasurementType.VMIN: "MIN",
    ScopeMeasurementType.VAVG: "MEAN",
    ScopeMeasurementType.VRMS: "RMS",
    ScopeMeasurementType.FREQUENCY: "FREQ",
    ScopeMeasurementType.PERIOD: "PER",
    ScopeMeasurementType.DUTY_CYCLE: "DUTY",
}

_TRIGGER_STATUS_MAP = {
    "ARM": TriggerStatus.ARMED,
    "READY": TriggerStatus.READY,
    "TRIG'D": TriggerStatus.TRIGGERED,
    "TRIGGERED": TriggerStatus.TRIGGERED,
    "AUTO": TriggerStatus.AUTO,
    "STOP": TriggerStatus.READY,
    "ROLL": TriggerStatus.SCAN,
}

# Command-error (CMR?) and execution-error (EXR?) code tables; 0 means no error.
_CMR_CODES = {
    1: "Unrecognized command/query header",
    2: "Invalid character",
    3: "Invalid separator",
    4: "Missing parameter",
    5: "Unrecognized keyword",
    6: "String error",
    7: "Parameter not allowed",
    8: "Command string too long",
    9: "Query not allowed",
    10: "Missing query mask",
    11: "Invalid parameter",
    12: "Parameter syntax error",
    13: "Filename too long",
}

_EXR_CODES = {
    21: "Permission error",
    22: "Environment error",
    23: "Option error",
    25: "Parameter error",
    26: "Non-implemented command",
    32: "Waveform descriptor error",
    36: "Panel setup error",
}

# SI multipliers used by SARA? (sample rate), which carries a unit suffix.
_SI_MULTIPLIERS = {"K": 1e3, "M": 1e6, "G": 1e9}

# Codes-per-division and horizontal divisions for the SDS1000X-E grid.
_CODES_PER_DIV = 25.0
_HORIZONTAL_DIVISIONS = 14

# IEEE-488.2 "not a number" sentinel for an invalid/unavailable measurement.
_VENDOR_INVALID_MEASUREMENT = 9.91e37


def _convert_sentinel(value: float) -> float:
    """Map the vendor invalid-measurement sentinel to NaN."""
    if abs(value) >= _VENDOR_INVALID_MEASUREMENT:
        return math.nan
    return value


def _parse_trailing_float(resp: str) -> float:
    """Parse the value after the last comma, stripping any trailing unit (V, S, Hz, %)."""
    token = resp.strip().split(",")[-1].strip()
    while token and token[-1] not in "0123456789.eE+-":
        token = token[:-1]
    return float(token)


def _parse_ieee_block(raw: bytes) -> bytes:
    """Extract the payload from an IEEE-488.2 definite-length block (``#<ndigits><length><payload>``)."""
    hash_idx = raw.find(b"#")
    if hash_idx < 0:
        raise ValueError("no IEEE-488.2 block header in reply")
    ndigits = int(chr(raw[hash_idx + 1]))
    length = int(raw[hash_idx + 2 : hash_idx + 2 + ndigits])
    start = hash_idx + 2 + ndigits
    return raw[start : start + length]


def _parse_sample_rate(resp: str) -> float:
    """Parse a SARA? reply (e.g. ``1.00GSa/s``, ``500.0kSa``, or bare ``1.00E+09``) to samples/sec."""
    token = resp.strip().upper()
    if token.startswith("SARA"):
        token = token[4:].strip()
    token = token.replace("SA/S", "").replace("SA", "").strip()
    if token and token[-1] in _SI_MULTIPLIERS:
        return float(token[:-1]) * _SI_MULTIPLIERS[token[-1]]
    return float(token)


class SiglentSDS1000XE(ScopeDriverBase):
    """SCPI driver for Siglent SDS1000X-E series oscilloscopes (SDS1104X-E and family)."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._trigger_source: int | None = None
        self._trigger_type: TriggerType = TriggerType.EDGE
        self._average_count: int = 16

    def open(self) -> None:
        """Open the transport and disable comm headers so query replies are bare values."""
        self._visa.open()
        self._visa.write("CHDR OFF")
        self._visa.write("*CLS")

    def close(self) -> None:
        self._visa.close()

    def _consume_trailing_terminator(self) -> None:
        """Drain the lone LF the SDS appends after a binary block; it would otherwise desync the next query."""
        try:
            with self._visa.temporary_timeout(400):
                self._visa.read_raw()
        except Exception:  # noqa: BLE001 - nothing buffered is the normal case
            pass

    def _read_binary_message(self, command: str) -> bytes:
        """Read a full binary reply with the read terminator disabled.

        SDS binary blocks (``SCDP`` BMP, ``PNSU?`` setup) embed LF bytes, so the transport's
        terminator-aware reads truncate them. Reach the pyvisa resource directly to read to EOM.
        """
        with self._visa.lock():
            inst = self._visa._inst  # noqa: SLF001 - escape hatch for terminator-free binary reads
            if inst is None:
                raise RuntimeError("SiglentSDS1000XE transport is not open")
            saved_termination = inst.read_termination
            inst.read_termination = None
            try:
                self._visa.write(command)
                return inst.read_raw()
            finally:
                inst.read_termination = saved_termination

    def check_errors(self) -> None:
        """Poll ``CMR?`` then ``EXR?`` and raise on the first non-zero code (no error queue on Siglent)."""
        cmr = int(self._visa.query("CMR?"))
        if cmr != 0:
            raise RuntimeError(f"Siglent command error {cmr}: {_CMR_CODES.get(cmr, 'Unknown command error')}")
        exr = int(self._visa.query("EXR?"))
        if exr != 0:
            raise RuntimeError(f"Siglent execution error {exr}: {_EXR_CODES.get(exr, 'Unknown execution error')}")

    # --- Channel vertical settings ---

    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        self._visa.write(f"C{channel}:VDIV {volts_per_div:.4E}")

    def get_vertical_scale(self, channel: int) -> float:
        return float(self._visa.query(f"C{channel}:VDIV?"))

    def set_vertical_offset(self, offset: float, channel: int) -> None:
        self._visa.write(f"C{channel}:OFST {offset:.4E}")

    def get_vertical_offset(self, channel: int) -> float:
        return float(self._visa.query(f"C{channel}:OFST?"))

    def set_coupling(self, coupling: Coupling, channel: int) -> None:
        self._visa.write(f"C{channel}:CPL {_COUPLING_TO_SCPI[coupling]}")

    def get_coupling(self, channel: int) -> Coupling:
        resp = self._visa.query(f"C{channel}:CPL?").strip().upper()
        if resp.startswith("A"):
            return Coupling.AC
        return Coupling.DC

    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        self._visa.write(f"C{channel}:ATTN {factor:g}")

    def get_probe_attenuation(self, channel: int) -> float:
        return float(self._visa.query(f"C{channel}:ATTN?"))

    # --- Horizontal (timebase) settings ---

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        self._visa.write(f"TDIV {seconds_per_div:.4E}")

    def get_horizontal_scale(self) -> float:
        return float(self._visa.query("TDIV?"))

    # --- Sample rate ---

    def get_sample_rate(self) -> float:
        return _parse_sample_rate(self._visa.query("SARA?"))

    # --- Acquisition ---

    def set_acquisition_mode(self, mode: AcquisitionMode) -> None:
        # The scope only applies ACQW while acquiring; call run() first if it's stopped.
        if mode == AcquisitionMode.ENVELOPE:
            raise NotImplementedError("ENVELOPE acquisition mode is not supported on Siglent SDS1000X-E series")
        if mode == AcquisitionMode.AVERAGE:
            # AVERAGE is ignored without an inline count.
            self._visa.write(f"ACQW AVERAGE,{self._average_count}")
        else:
            self._visa.write(f"ACQW {_ACQ_MODE_TO_SCPI[mode]}")

    def get_acquisition_mode(self) -> AcquisitionMode:
        resp = self._visa.query("ACQW?").strip().upper().split(",")[0]
        return _SCPI_TO_ACQ_MODE.get(resp, AcquisitionMode.NORMAL)

    def set_average_count(self, count: int) -> None:
        self._average_count = count
        self._visa.write(f"AVGA {count}")

    def get_average_count(self) -> int:
        return int(float(self._visa.query("AVGA?")))

    def run(self) -> None:
        # ARM would force single-shot mode; TRMD AUTO alone free-runs continuously.
        self._visa.write("TRMD AUTO")

    def stop(self) -> None:
        self._visa.write("STOP")

    def single(self) -> None:
        self._visa.write("TRMD SINGLE")
        self._visa.write("ARM")

    def digitize(self, timeout: float) -> None:
        """Arm a single acquisition then poll ``INR?`` bit 0 (new signal acquired) until set or ``timeout``."""
        self._visa.write("TRMD SINGLE")
        self._visa.write("ARM")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if int(self._visa.query("INR?")) & 0x1:
                return
            time.sleep(0.05)
        self._visa.write("STOP")
        raise TimeoutError(
            f"Acquisition did not complete within {timeout}s. The trigger condition may not have been met."
        )

    def get_acquisition_state(self) -> AcquisitionState:
        resp = self._visa.query("SAST?").strip().upper()
        if resp.startswith("STOP"):
            return AcquisitionState.STOPPED
        return AcquisitionState.RUNNING

    # --- Waveform data ---

    def fetch_waveform(self, channel: int) -> WaveformData:
        """Fetch the full waveform from ``channel`` over ``C{n}:WF? DAT2`` (signed 8-bit codes)."""
        self._visa.write("WFSU SP,0,NP,0,FP,0")
        # Check errors before the data query — a bad setup command would otherwise hang the read.
        self.check_errors()

        vdiv = float(self._visa.query(f"C{channel}:VDIV?"))
        offset = float(self._visa.query(f"C{channel}:OFST?"))
        timebase = float(self._visa.query("TDIV?"))
        trigger_delay = float(self._visa.query("TRDL?"))
        sample_rate = _parse_sample_rate(self._visa.query("SARA?"))

        codes = self._visa.query_binary_values(f"C{channel}:WF? DAT2", datatype="b", container=list)
        self._consume_trailing_terminator()

        x_origin_ns = int((trigger_delay - timebase * _HORIZONTAL_DIVISIONS / 2) * 1e9)
        x_incr_ns = int((1.0 / sample_rate) * 1e9)

        times = [x_origin_ns + i * x_incr_ns for i in range(len(codes))]
        voltages = [code * vdiv / _CODES_PER_DIV - offset for code in codes]

        return WaveformData(times=times, voltages=voltages)

    # --- Measurements ---

    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        """Query a built-in parameter via ``C{n}:PAVA?``; unavailable/sentinel results map to ``NaN``."""
        param = _MEAS_TYPE_TO_SCPI[measurement_type]
        resp = self._visa.query(f"C{channel}:PAVA? {param}")
        try:
            return _convert_sentinel(_parse_trailing_float(resp))
        except ValueError:
            return math.nan

    # --- Trigger ---

    def _write_trigger_select(self) -> None:
        """Re-emit ``TRSE`` with the cached type+source (Siglent sets both in one command)."""
        source = self._trigger_source if self._trigger_source is not None else 1
        type_token = _TRIGGER_TYPE_TO_SCPI[self._trigger_type]
        self._visa.write(f"TRSE {type_token},SR,C{source},HT,OFF")

    def set_trigger_source(self, channel: int) -> None:
        self._trigger_source = channel
        self._write_trigger_select()

    def set_trigger_type(self, trigger_type: TriggerType) -> None:
        self._trigger_type = trigger_type
        self._write_trigger_select()

    def set_trigger_level(self, level: float) -> None:
        source = self._trigger_source if self._trigger_source is not None else 1
        self._visa.write(f"C{source}:TRLV {level:.4E}")

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        source = self._trigger_source if self._trigger_source is not None else 1
        self._visa.write(f"C{source}:TRSL {_TRIGGER_SLOPE_TO_SCPI[slope]}")

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        self._visa.write(f"TRMD {_TRIGGER_MODE_TO_SCPI[mode]}")

    def force_trigger(self) -> None:
        self._visa.write("FRTR")

    def get_trigger_status(self) -> TriggerStatus:
        resp = self._visa.query("SAST?").strip().upper()
        return _TRIGGER_STATUS_MAP.get(resp, TriggerStatus.ARMED)

    # --- File operations ---

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        """Transfer a screen dump (``SCDP``, a raw BMP) to the host and write it to ``filepath``."""
        if to_instrument:
            raise NotImplementedError("SDS1000X-E SCDP is host-transfer only; saving to the instrument is unsupported")
        raw = self._read_binary_message("SCDP")
        size = int.from_bytes(raw[2:6], "little")  # BMP header: 'BM' then a little-endian file size
        data = raw[:size]
        with open(filepath, "wb") as f:
            f.write(data)
        return data

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        """Save the panel setup to a USB file (``STPN``) or transfer it to the host (``PNSU?``)."""
        if to_instrument:
            self._visa.write(f"STPN DISK,UDSK,FILE,'{name}'")
            return b""
        data = _parse_ieee_block(self._read_binary_message("PNSU?"))
        with open(name, "wb") as f:
            f.write(data)
        return data

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        """Recall a panel setup from a USB file (``RCPN``) or push host bytes back (``PNSU``).

        The host-side ``PNSU`` write-back is known to wedge the USBTMC interface on some
        SDS1000X-E firmware; prefer ``from_instrument=True`` (USB stick) over USB connections.
        """
        if from_instrument:
            self._visa.write(f"RCPN DISK,UDSK,FILE,'{name}'")
            return
        with open(name, "rb") as f:
            data = f.read()
        header = f"#9{len(data):09d}".encode()
        self._visa.write_raw(b"PNSU " + header + data + b"\n")
