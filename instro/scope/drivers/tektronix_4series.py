"""Tektronix 4 Series MSO (MSO44, MSO46).

``Tektronix4SeriesMSO`` owns the one VISA session and vends the two views: ``.scope`` for
``InstroScope`` and ``.awg`` for ``InstroAWG`` (the built-in AFG, option AFG).
"""

from __future__ import annotations

import calendar
import math
import re
import struct
import threading
import time

from instro.awg import AWGDriverBase
from instro.awg.types import (
    AmplitudeMeasurementUnit,
    ModulationType,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    SweepTriggerSource,
    SweepType,
    Triangle,
    Waveform,
)
from instro.lib.exceptions import FeatureNotSupportedError
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

FRIENDLY_NAME = "Tektronix 4 MSO Series"

_SAWTOOTH_SYMMETRY_PCT = 100
_TRIANGLE_SYMMETRY_PCT = 50
_NO_MODULATION = "the MSO4 AFG has no modulation"
_NO_SWEEP = "the MSO4 AFG has no frequency sweep"
_NO_ALIGN_PHASE = "the MSO4 AFG has a single output, so there is no phase to align"

# FastFrame timestamps look like ``"1: 20.10.2026.12:10:19.020320320070"`` (DD.MM.YYYY.HH:MM:SS.<12 fractional digits>).
_FRAME_TIMESTAMP_RE = re.compile(r"\d+:\s*(\d{2})\.(\d{2})\.(\d{4})\.(\d{2}):(\d{2}):(\d{2})\.(\d+)")
_FRAME_XZERO_RE = re.compile(r"\d+:\s*([-+0-9.eE]+)")

_ACQ_MODE_TO_SCPI = {
    AcquisitionMode.NORMAL: "SAMple",
    AcquisitionMode.AVERAGE: "AVErage",
    AcquisitionMode.HIGH_RESOLUTION: "HIRes",
    AcquisitionMode.PEAK_DETECT: "PEAKdetect",
    AcquisitionMode.ENVELOPE: "ENVelope",
}

_SCPI_TO_ACQ_MODE = {v.upper(): k for k, v in _ACQ_MODE_TO_SCPI.items()}

_COUPLING_TO_SCPI = {
    Coupling.AC: "AC",
    Coupling.DC: "DC",
}

_TRIGGER_TYPE_TO_SCPI = {
    TriggerType.EDGE: "EDGE",
    TriggerType.PULSE: "WIDth",
}

_TRIGGER_SLOPE_TO_SCPI = {
    TriggerSlope.RISING: "RISe",
    TriggerSlope.FALLING: "FALL",
    TriggerSlope.EITHER: "EITher",
}

_TRIGGER_MODE_TO_SCPI = {
    TriggerMode.AUTO: "AUTO",
    TriggerMode.NORMAL: "NORMal",
}

_MEAS_TYPE_TO_SCPI = {
    ScopeMeasurementType.VPP: "PK2Pk",
    ScopeMeasurementType.VMAX: "MAXIMUM",
    ScopeMeasurementType.VMIN: "MINIMUM",
    ScopeMeasurementType.VAVG: "MEAN",
    ScopeMeasurementType.VRMS: "RMS",
    ScopeMeasurementType.FREQUENCY: "FREQUENCY",
    ScopeMeasurementType.PERIOD: "PERIOD",
    ScopeMeasurementType.DUTY_CYCLE: "PDUTY",
}

_TRIGGER_STATUS_MAP = {
    "ARMED": TriggerStatus.ARMED,
    "AUTO": TriggerStatus.AUTO,
    "READY": TriggerStatus.READY,
    "SAVE": TriggerStatus.SAVE,
    "TRIGGER": TriggerStatus.TRIGGERED,
}

# IEEE-488.2 "not a number" sentinel returned by SCPI scopes when a
# measurement has no valid result yet (e.g. slot newly created, no
# acquisition since slot was added).
_VENDOR_INVALID_MEASUREMENT = 9.91e37


def _convert_sentinel(value: float) -> float:
    """Map the vendor invalid-measurement sentinel to NaN."""
    if abs(value) >= _VENDOR_INVALID_MEASUREMENT:
        return math.nan
    return value


class Tektronix4SeriesMSO:
    """One MSO4: owns the VISA session, the error queue, and the response-format setup shared by its views."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        """Construct from a VISA resource string (uses defaults) or a full ``VisaConfig``."""
        self._visa = VisaDriver(visa_resource)
        self._ownership = threading.RLock()
        self._views: list[object] = []
        self.scope: ScopeDriverBase = Tektronix4SeriesMSOScope(self)
        self.awg: AWGDriverBase = Tektronix4SeriesMSOAFG(self)

    def _acquire(self, view: object) -> None:
        """Admit a view; the first one in opens the session and pins the response format."""
        with self._ownership:
            if any(held is view for held in self._views):
                return
            first = not self._views
            self._views.append(view)
            if not first:
                return
            try:
                self._visa.open(self)  # the device holds the transport, not its views
                with self._visa.lock():
                    self._visa.write("*CLS")
                    self._visa.write("HEADer OFF")
                    self._visa.write("VERBose ON")
                    self._check_errors()
            except BaseException:
                # Leave nothing behind, or a retry reports not-first and skips this setup.
                self._views = [held for held in self._views if held is not view]
                self._visa.close(self)
                raise

    def _release(self, view: object) -> None:
        """Drop a view; the last one out closes the session."""
        with self._ownership:
            self._views = [held for held in self._views if held is not view]
            if self._views:
                return
            self._visa.close(self)

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _query_checked(self, command: str) -> str:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
        return value

    def _check_errors(self) -> None:
        """Read ``*ESR?`` to publish pending events, then drain ``ALLEv?`` and raise on any error event.

        Codes 0-1 mean an empty queue, 400-405 and 468/472 are informational events (e.g. 401 power-on),
        500-999 are warnings (e.g. 547 measurement clipping), and 2200-2231 are measurement errors, so none of those raise.
        """
        with self._visa.lock():
            self._visa.query("*ESR?")
            # Event text can carry non-ASCII characters that the transport's ASCII-decoding query rejects.
            resp = self._visa.query_raw("ALLEv?").decode("utf-8", errors="replace")
        # Messages embed ';' and ',', so locate codes by the quoted message that follows each one.
        errors = [code for code in re.findall(r'(\d+)\s*,\s*"', resp) if _is_error_code(int(code))]
        if errors:
            raise RuntimeError(f"{FRIENDLY_NAME} SCPI errors: {resp.strip()}")


class Tektronix4SeriesMSOScope(ScopeDriverBase):
    """Oscilloscope view of a ``Tektronix4SeriesMSO``. Construct via ``Tektronix4SeriesMSO(...).scope``."""

    def __init__(self, device: Tektronix4SeriesMSO) -> None:
        self._device = device
        self._visa = device._visa
        self._trigger_source: int | None = None
        self._measurement_slots: dict[tuple[ScopeMeasurementType, int], str] = {}

    def open(self) -> None:
        self._device._acquire(self)

    def close(self) -> None:
        self._device._release(self)

    def check_errors(self) -> None:
        self._device._check_errors()

    # --- Channel vertical settings ---

    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        self._visa.write(f"CH{channel}:SCAle {volts_per_div}")

    def get_vertical_scale(self, channel: int) -> float:
        return float(self._visa.query(f"CH{channel}:SCAle?"))

    def set_vertical_offset(self, offset: float, channel: int) -> None:
        self._visa.write(f"CH{channel}:OFFSet {offset}")

    def get_vertical_offset(self, channel: int) -> float:
        return float(self._visa.query(f"CH{channel}:OFFSet?"))

    def set_coupling(self, coupling: Coupling, channel: int) -> None:
        self._visa.write(f"CH{channel}:COUPling {_COUPLING_TO_SCPI[coupling]}")

    def get_coupling(self, channel: int) -> Coupling:
        resp = self._visa.query(f"CH{channel}:COUPling?").strip().upper()
        if resp == "AC":
            return Coupling.AC
        if resp == "DC":
            return Coupling.DC
        raise ValueError(f"Unsupported coupling {resp!r} on CH{channel}")

    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        self._visa.write(f"CH{channel}:PROBEFunc:EXTAtten {factor}")

    def get_probe_attenuation(self, channel: int) -> float:
        return float(self._visa.query(f"CH{channel}:PROBEFunc:EXTAtten?"))

    # --- Horizontal (timebase) settings ---

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        self._visa.write(f"HORizontal:SCAle {seconds_per_div}")

    def get_horizontal_scale(self) -> float:
        return float(self._visa.query("HORizontal:SCAle?"))

    # --- Sample rate ---

    def get_sample_rate(self) -> float:
        return float(self._visa.query("HORizontal:SAMPLERate?"))

    # --- Acquisition ---

    def set_acquisition_mode(self, mode: AcquisitionMode) -> None:
        self._visa.write(f"ACQuire:MODe {_ACQ_MODE_TO_SCPI[mode]}")

    def get_acquisition_mode(self) -> AcquisitionMode:
        resp = self._visa.query("ACQuire:MODe?").strip().upper()
        return _SCPI_TO_ACQ_MODE.get(resp, AcquisitionMode.NORMAL)

    def set_average_count(self, count: int) -> None:
        self._visa.write(f"ACQuire:NUMAVg {count}")

    def get_average_count(self) -> int:
        return int(float(self._visa.query("ACQuire:NUMAVg?")))

    def run(self) -> None:
        self._visa.write("ACQuire:STOPAfter RUNSTop")
        self._visa.write("ACQuire:STATE RUN")

    def stop(self) -> None:
        self._visa.write("ACQuire:STATE STOP")

    def single(self) -> None:
        self._visa.write("ACQuire:STOPAfter SEQuence")
        self._visa.write("ACQuire:STATE RUN")

    def digitize(self, timeout: float) -> None:
        """``SEQuence``+``RUN``, then ``*OPC?`` under a scoped VISA timeout. ``clear()`` on timeout to restore the session."""
        self._visa.write("ACQuire:STOPAfter SEQuence")
        self._visa.write("ACQuire:STATE RUN")
        try:
            with self._visa.temporary_timeout(int(timeout * 1000)):
                self._visa.query("*OPC?")
        except Exception as exc:
            self._visa.clear()
            raise TimeoutError(
                f"Acquisition did not complete within {timeout}s. The trigger condition may not have been met."
            ) from exc

    def get_acquisition_state(self) -> AcquisitionState:
        resp = self._visa.query("ACQuire:STATE?").strip()
        if resp == "1":
            return AcquisitionState.RUNNING
        return AcquisitionState.STOPPED

    # --- Waveform data ---

    def fetch_waveform(self, channel: int) -> WaveformData:
        """Fetch the full record from ``channel`` over ``RIBinary`` (signed 16-bit); a FastFrame burst is fetched whole.

        Times are relative to the trigger; for a burst, to the last frame's trigger. In roll mode there is no
        trigger, so ``t = 0`` is the newest sample. The driver reads FastFrame but never configures it.
        """
        with self._visa.lock():
            if self._visa.query("HORizontal:FASTframe:STATE?").strip() == "1":
                self._visa.write(f"DATa:SOUrce CH{channel}")
                self._visa.write("DATa:ENCdg RIBinary")
                self._visa.write("WFMOutpre:BYT_Nr 2")
                record_length = int(float(self._visa.query("HORizontal:RECOrdlength?")))
                timestamps = _parse_frame_timestamps_ns(self._visa.query("HORizontal:FASTframe:TIMEStamp:ALL?"))
                x_zeros = [
                    float(x) for x in _FRAME_XZERO_RE.findall(self._visa.query("HORizontal:FASTframe:XZEro:ALL?"))
                ]
                # DATa:STOP and DATa:FRAMESTOP persist, so rewrite the whole window on every fetch.
                self._visa.write("DATa:STARt 1")
                self._visa.write(f"DATa:STOP {record_length}")
                self._visa.write("DATa:FRAMESTARt 1")
                self._visa.write(f"DATa:FRAMESTOP {len(timestamps)}")
                self.check_errors()

                x_incr = float(self._visa.query("WFMOutpre:XINcr?"))
                pt_off = float(self._visa.query("WFMOutpre:PT_Off?"))  # assumed identical for every frame
                y_mult = float(self._visa.query("WFMOutpre:YMUlt?"))
                y_off = float(self._visa.query("WFMOutpre:YOFf?"))
                y_zero = float(self._visa.query("WFMOutpre:YZEro?"))

                with self._visa.temporary_timeout(30000):  # a burst is many records in one reply
                    blocks = _split_curve_blocks(self._visa.query_raw("CURVe?"))
                if not len(blocks) == len(timestamps) == len(x_zeros):
                    raise RuntimeError(
                        f"FastFrame returned {len(blocks)} frames, {len(timestamps)} timestamps, {len(x_zeros)} XZero values"
                    )

                times: list[int] = []
                voltages: list[float] = []
                for timestamp, x_zero, block in zip(timestamps, x_zeros, blocks):
                    points = struct.unpack(f">{len(block) // 2}h", block)
                    frame_offset_ns = timestamp - timestamps[-1]
                    times.extend(
                        round(frame_offset_ns + ((i - pt_off) * x_incr + x_zero) * 1e9) for i in range(len(points))
                    )
                    voltages.extend((pt - y_off) * y_mult + y_zero for pt in points)

                return WaveformData(times=times, voltages=voltages)

            self._visa.write(f"DATa:SOUrce CH{channel}")
            self._visa.write("DATa:ENCdg RIBinary")
            self._visa.write("WFMOutpre:BYT_Nr 2")
            record_length = int(float(self._visa.query("HORizontal:RECOrdlength?")))
            # DATa:STOP persists and does not follow record length changes, so rewrite it on every fetch.
            self._visa.write("DATa:STARt 1")
            self._visa.write(f"DATa:STOP {record_length}")
            # A failed setup command would leave the data query waiting for a reply that never comes.
            self.check_errors()

            x_incr = float(self._visa.query("WFMOutpre:XINcr?"))
            x_zero = float(
                self._visa.query("WFMOutpre:XZEro?")
            )  # sub-sample time from the trigger sample to the trigger
            pt_off = float(self._visa.query("WFMOutpre:PT_Off?"))
            y_mult = float(self._visa.query("WFMOutpre:YMUlt?"))
            y_off = float(self._visa.query("WFMOutpre:YOFf?"))  # always zero on this family, per manual
            y_zero = float(self._visa.query("WFMOutpre:YZEro?"))
            rolling = self._visa.query("HORizontal:ROLL?").strip().upper() not in ("0", "OFF")

            points = self._visa.query_binary_values("CURVe?", datatype="h", is_big_endian=True, container=list)

            if rolling:
                # PT_Off is not a real trigger position in roll mode; anchoring the newest sample at 0 keeps a free-run
                # fetch from being stamped into the future.
                last = len(points) - 1
                times = [round((i - last) * x_incr * 1e9) for i in range(len(points))]
            else:
                times = [round(((i - pt_off) * x_incr + x_zero) * 1e9) for i in range(len(points))]
            voltages = [(pt - y_off) * y_mult + y_zero for pt in points]

            return WaveformData(times=times, voltages=voltages)

    # --- Measurements ---

    def setup_measurement(self, measurement_type: ScopeMeasurementType, channel: int) -> None:
        """Ensure a persistent measurement slot exists for this type and channel.

        Uses ``MEASUrement:ADDMEAS <type>`` to create the slot atomically with
        the target type — this avoids the race where ``ADDNew "MEAS<n>"`` +
        ``MEAS<n>:TYPe`` creates a slot that briefly computes under the scope's
        default type (typically Period) before the type change takes effect,
        causing the first ``measure()`` call to return a stale, wrong-type
        value. ``ADDMEAS`` doesn't take a name; the new slot's name (the scope
        auto-assigns the next ``MEAS<n>``) is recovered by diffing
        ``MEASUrement:LIST?`` before and after the add.

        Tektronix computes measurements during acquisition, so the slot
        must exist before the scope triggers for results to be valid.
        """
        key = (measurement_type, channel)
        if key in self._measurement_slots:
            return

        scpi_type = _MEAS_TYPE_TO_SCPI[measurement_type]
        with self._visa.lock():
            before = self._list_measurements()
            self._visa.write(f"MEASUrement:ADDMEAS {scpi_type}")
            after = self._list_measurements()
            new_names = after - before
            if len(new_names) != 1:
                raise RuntimeError(
                    f"Expected one new measurement after MEASUrement:ADDMEAS {scpi_type}, "
                    f"got {sorted(new_names)} (before={sorted(before)}, after={sorted(after)})"
                )
            meas_name = new_names.pop()
            self._visa.write(f"MEASUrement:{meas_name}:SOUrce1 CH{channel}")
            self.check_errors()
            self._measurement_slots[key] = meas_name

        self._wait_for_measurement_ready(meas_name, timeout=2.0)

    def _list_measurements(self) -> set[str]:
        """Names of all measurement slots currently defined on the scope."""
        raw = self._visa.query("MEASUrement:LIST?").strip()
        if not raw or raw.upper() == "NONE":
            return set()
        return {name.strip().strip('"') for name in raw.split(",") if name.strip()}

    def clear_measurements(self) -> None:
        """Delete every measurement slot on the scope and reset the local cache."""
        for name in self._list_measurements():
            self._visa.write(f'MEASUrement:DELete "{name}"')
        self._measurement_slots.clear()

    def _wait_for_measurement_ready(self, meas_name: str, timeout: float) -> None:
        """Poll until a newly-added measurement slot reports a non-sentinel value.

        Tek's measurement engine reprocesses the current acquisition through a
        newly-added slot asynchronously. Querying the result immediately after
        ``ADDNew`` races that compute and returns the sentinel. Polling until
        the value is real makes the first ``measure()`` call return useful data.

        If the timeout elapses (e.g. NORMAL trigger with no signal — no
        acquisition for the new slot to compute against), returns silently.
        The caller will see NaN from ``measure()``.
        """
        query = f"MEASUrement:{meas_name}:RESUlts:CURRentacq:MEAN?"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if abs(float(self._visa.query(query))) < _VENDOR_INVALID_MEASUREMENT:
                    return
            except ValueError:
                pass
            time.sleep(0.05)

    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        """Read a built-in measurement from a persistent slot; the vendor's invalid sentinel maps to ``NaN``."""
        self.setup_measurement(measurement_type, channel)
        meas_name = self._measurement_slots[(measurement_type, channel)]
        value = float(self._visa.query(f"MEASUrement:{meas_name}:RESUlts:CURRentacq:MEAN?"))
        return _convert_sentinel(value)

    # --- Trigger ---

    def set_trigger_source(self, channel: int) -> None:
        self._visa.write(f"TRIGger:A:EDGE:SOUrce CH{channel}")
        self._trigger_source = channel

    def set_trigger_type(self, trigger_type: TriggerType) -> None:
        self._visa.write(f"TRIGger:A:TYPe {_TRIGGER_TYPE_TO_SCPI[trigger_type]}")

    def set_trigger_level(self, level: float) -> None:
        source = self._trigger_source if self._trigger_source is not None else 1
        self._visa.write(f"TRIGger:A:LEVel:CH{source} {level}")

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        self._visa.write(f"TRIGger:A:EDGE:SLOpe {_TRIGGER_SLOPE_TO_SCPI[slope]}")

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        self._visa.write(f"TRIGger:A:MODe {_TRIGGER_MODE_TO_SCPI[mode]}")

    def force_trigger(self) -> None:
        self._visa.write("TRIGger FORCe")

    def get_trigger_status(self) -> TriggerStatus:
        resp = self._visa.query("TRIGger:STATE?").strip().upper()
        return _TRIGGER_STATUS_MAP.get(resp, TriggerStatus.ARMED)

    # --- File operations ---

    def _read_file_from_instrument(self, instrument_path: str) -> bytes:
        """``FILESystem:READFile`` from the scope's filesystem with a 30 s timeout (file transfers can be slow)."""
        with self._visa.temporary_timeout(30000):  # File transfers can be slow
            self._visa.write(f'FILESystem:READFile "{instrument_path}"')
            return self._visa.read_raw()

    def _write_file_to_instrument(self, instrument_path: str, data: bytes) -> None:
        """``FILESystem:WRITEFile`` to the scope's filesystem, with ``data`` as an IEEE 488.2 definite-length block."""
        length = str(len(data))
        header = f'FILESystem:WRITEFile "{instrument_path}",#{len(length)}{length}'.encode()
        self._visa.write_raw(header + data)

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        if to_instrument:
            self._visa.write(f'SAVe:IMAGe "{filepath}"')
            with self._visa.temporary_timeout(30000):
                self._visa.query("*OPC?")
            return b""
        # Save to scope temp storage, wait for completion, transfer to host, clean up
        temp_path = "C:/nominal_temp_screenshot.png"
        self._visa.write(f'SAVe:IMAGe "{temp_path}"')
        with self._visa.temporary_timeout(30000):
            self._visa.query("*OPC?")  # Block until save completes
        self.check_errors()
        data = self._read_file_from_instrument(temp_path)
        self._visa.write(f'FILESystem:DELete "{temp_path}"')
        with open(filepath, "wb") as f:
            f.write(data)
        return data

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        if to_instrument:
            self._visa.write(f'SAVe:SETUp "{name}"')
            with self._visa.temporary_timeout(30000):
                self._visa.query("*OPC?")
            return b""
        # Save to scope temp storage, wait for completion, transfer to host, clean up
        temp_path = "C:/nominal_temp_setup.set"
        self._visa.write(f'SAVe:SETUp "{temp_path}"')
        with self._visa.temporary_timeout(30000):
            self._visa.query("*OPC?")
        self.check_errors()
        data = self._read_file_from_instrument(temp_path)
        self._visa.write(f'FILESystem:DELete "{temp_path}"')
        with open(name, "wb") as f:
            f.write(data)
        return data

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        if from_instrument:
            self._visa.write(f'RECAll:SETUp "{name}"')
            return
        # Upload to scope temp storage, recall from there, clean up
        temp_path = "C:/nominal_temp_setup.set"
        with open(name, "rb") as f:
            data = f.read()
        self._write_file_to_instrument(temp_path, data)
        self._visa.write(f'RECAll:SETUp "{temp_path}"')
        with self._visa.temporary_timeout(30000):
            self._visa.query("*OPC?")
        self._visa.write(f'FILESystem:DELete "{temp_path}"')


class Tektronix4SeriesMSOAFG(AWGDriverBase):
    """AFG view of a ``Tektronix4SeriesMSO``. Construct via ``Tektronix4SeriesMSO(...).awg``."""

    def __init__(self, device: Tektronix4SeriesMSO) -> None:
        self._device = device
        self._visa = device._visa

    def open(self) -> None:
        self._device._acquire(self)

    def close(self) -> None:
        self._device._release(self)

    def set_waveform(self, channel: int, waveform: Waveform) -> None:
        _check_channel(channel)
        match waveform:
            case Sine():
                _require_zero_phase(waveform.phase_deg)
                commands = ["AFG:FUNCtion SINE", f"AFG:FREQuency {waveform.frequency_hz}"]
            case Square():
                _require_zero_phase(waveform.phase_deg)
                if not 10.0 <= waveform.duty_cycle_pct <= 90.0:
                    raise FeatureNotSupportedError(
                        f"the MSO4 AFG cannot produce a {waveform.duty_cycle_pct}% square duty cycle; it supports 10 to 90 percent"
                    )
                commands = [
                    "AFG:FUNCtion SQUare",
                    f"AFG:FREQuency {waveform.frequency_hz}",
                    f"AFG:SQUare:DUty {waveform.duty_cycle_pct}",
                ]
            case Sawtooth():
                _require_zero_phase(waveform.phase_deg)
                commands = [
                    "AFG:FUNCtion RAMP",
                    f"AFG:FREQuency {waveform.frequency_hz}",
                    f"AFG:RAMP:SYMmetry {_SAWTOOTH_SYMMETRY_PCT}",
                ]
            case Triangle():
                _require_zero_phase(waveform.phase_deg)
                commands = [
                    "AFG:FUNCtion RAMP",
                    f"AFG:FREQuency {waveform.frequency_hz}",
                    f"AFG:RAMP:SYMmetry {_TRIANGLE_SYMMETRY_PCT}",
                ]
            case Pulse():
                if waveform.delay_s != 0.0:
                    raise FeatureNotSupportedError("the MSO4 AFG cannot program a pulse delay")
                commands = [
                    "AFG:FUNCtion PULSe",
                    f"AFG:FREQuency {waveform.frequency_hz}",
                    f"AFG:PULse:WIDth {waveform.width_s}",
                ]
            case StaticValue():
                commands = ["AFG:FUNCtion DC", f"AFG:OFFSet {waveform.value}"]
            case _:
                raise NotImplementedError(
                    f"{type(waveform).__name__} waveforms are not yet implemented for the MSO4 AFG"
                )
        with self._visa.lock():
            for command in commands:
                self._visa.write(command)
            self._device._check_errors()

    def get_waveform(self, channel: int) -> Waveform:
        _check_channel(channel)
        with self._visa.lock():
            name = self._device._query_checked("AFG:FUNCtion?").strip().upper()
            if name == "DC":
                return StaticValue(value=float(self._device._query_checked("AFG:OFFSet?")))
            frequency = float(self._device._query_checked("AFG:FREQuency?"))
            if name == "SINE":
                return Sine(frequency_hz=frequency)
            if name == "SQUARE":
                duty = float(self._device._query_checked("AFG:SQUare:DUty?"))
                return Square(frequency_hz=frequency, duty_cycle_pct=duty)
            if name == "RAMP":
                symmetry = float(self._device._query_checked("AFG:RAMP:SYMmetry?"))
                if math.isclose(symmetry, _SAWTOOTH_SYMMETRY_PCT):
                    return Sawtooth(frequency_hz=frequency)
                if math.isclose(symmetry, _TRIANGLE_SYMMETRY_PCT):
                    return Triangle(frequency_hz=frequency)
                raise FeatureNotSupportedError(
                    f"MSO4 AFG ramp symmetry {symmetry}% is neither a sawtooth nor a triangle"
                )
            if name == "PULSE":
                width = float(self._device._query_checked("AFG:PULse:WIDth?"))
                return Pulse(frequency_hz=frequency, width_s=width)
        raise FeatureNotSupportedError(f"MSO4 AFG reported waveform '{name}', which the AWG types cannot represent")

    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
        _check_channel(channel)
        if unit is not AmplitudeMeasurementUnit.VPP:
            raise FeatureNotSupportedError(f"the MSO4 AFG programs amplitude in VPP only, not {unit.value}")
        self._device._write_checked(f"AFG:AMPLitude {amplitude}")

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        _check_channel(channel)
        return float(self._device._query_checked("AFG:AMPLitude?")), AmplitudeMeasurementUnit.VPP

    def set_offset(self, channel: int, offset: float) -> None:
        _check_channel(channel)
        self._device._write_checked(f"AFG:OFFSet {offset}")

    def get_offset(self, channel: int) -> float:
        _check_channel(channel)
        return float(self._device._query_checked("AFG:OFFSet?"))

    def output_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        self._device._write_checked(f"AFG:OUTPut:STATE {'ON' if enable else 'OFF'}")

    def get_output_state(self, channel: int) -> bool:
        _check_channel(channel)
        return self._device._query_checked("AFG:OUTPut:STATE?").strip() == "1"

    def set_output_load(self, channel: int, load: float | None) -> None:
        _check_channel(channel)
        if load is None:
            self._device._write_checked("AFG:OUTPut:LOAd:IMPEDance HIGHZ")
        elif load == 50:
            self._device._write_checked("AFG:OUTPut:LOAd:IMPEDance FIFty")
        else:
            raise FeatureNotSupportedError(f"the MSO4 AFG supports a 50 ohm or high-Z (None) load, not {load}")

    def get_output_load(self, channel: int) -> float | None:
        _check_channel(channel)
        resp = self._device._query_checked("AFG:OUTPut:LOAd:IMPEDance?").strip().upper()
        if resp == "HIGHZ":
            return None
        if resp == "FIFTY":
            return 50.0
        raise FeatureNotSupportedError(
            f"MSO4 AFG reported load impedance '{resp}', which the AWG types cannot represent"
        )

    def set_modulation(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float) -> None:
        raise FeatureNotSupportedError(_NO_MODULATION)

    def modulation_enable(self, channel: int, enable: bool) -> None:
        raise FeatureNotSupportedError(_NO_MODULATION)

    def get_modulation_type(self, channel: int) -> ModulationType:
        raise FeatureNotSupportedError(_NO_MODULATION)

    def get_modulation_state(self, channel: int) -> bool:
        raise FeatureNotSupportedError(_NO_MODULATION)

    def fire_sweep_trigger(self, channel: int) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def get_sweep_end_freq(self, channel: int) -> float:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def get_sweep_return_time(self, channel: int) -> float:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def get_sweep_start_freq(self, channel: int) -> float:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def get_sweep_start_hold_time(self, channel: int) -> float:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def get_sweep_state(self, channel: int) -> bool:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def get_sweep_stop_hold_time(self, channel: int) -> float:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def get_sweep_time(self, channel: int) -> float:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def get_sweep_trigger(self, channel: int) -> SweepTriggerSource:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def get_sweep_type(self, channel: int) -> SweepType:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def set_sweep(self, channel: int, sweep_type: SweepType) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def set_sweep_end_freq(self, channel: int, frequency_hz: float) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def set_sweep_return_time(self, channel: int, return_time: float) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def set_sweep_start_freq(self, channel: int, frequency_hz: float) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def set_sweep_start_hold_time(self, channel: int, hold_time: float) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def set_sweep_stop_hold_time(self, channel: int, hold_time: float) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def set_sweep_time(self, channel: int, sweep_time: float) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def set_sweep_trigger(self, channel: int, source: SweepTriggerSource) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def sweep_enable(self, channel: int, enable: bool) -> None:
        raise FeatureNotSupportedError(_NO_SWEEP)

    def align_phase(self) -> None:
        raise FeatureNotSupportedError(_NO_ALIGN_PHASE)


def _check_channel(channel: int) -> None:
    if channel != 1:
        raise FeatureNotSupportedError(f"the MSO4 AFG has a single output, not channel {channel}")


def _require_zero_phase(phase_deg: float) -> None:
    if phase_deg != 0.0:
        raise FeatureNotSupportedError("the MSO4 AFG cannot program a phase offset")


def _is_error_code(code: int) -> bool:
    """Command (1xx), execution (2xx), device (3xx), and query (410/420/430/440) errors, plus 1000+ errors.

    Measurement errors (2200-2231, e.g. "Low amplitude") are not raised: a measurement with no valid result reads back as
    the invalid-measurement sentinel, which ``measure`` maps to ``NaN``.
    """
    return 100 <= code < 400 or code in (410, 420, 430, 440) or (code >= 1000 and not 2200 <= code <= 2231)


def _parse_frame_timestamps_ns(resp: str) -> list[int]:
    """Absolute frame trigger times from ``TIMEStamp:ALL?``, in integer nanoseconds."""
    times = []
    for day, month, year, hour, minute, second, fraction in _FRAME_TIMESTAMP_RE.findall(resp):
        seconds = calendar.timegm((int(year), int(month), int(day), int(hour), int(minute), int(second), 0, 0, 0))
        times.append(seconds * 1_000_000_000 + int(fraction.ljust(9, "0")[:9]))
    return times


def _split_curve_blocks(raw: bytes) -> list[bytes]:
    """Split a multi-frame ``CURVe?`` reply into one IEEE 488.2 definite-length block payload per frame."""
    blocks = []
    pos = 0
    while pos < len(raw):
        if raw[pos : pos + 1] in (b";", b"\n"):  # frames are ';'-separated, the reply ends with LF
            pos += 1
            continue
        if raw[pos : pos + 1] != b"#":
            raise RuntimeError(f"Unexpected byte {raw[pos : pos + 1]!r} at offset {pos} in CURVe? reply")
        digits = int(raw[pos + 1 : pos + 2])
        length = int(raw[pos + 2 : pos + 2 + digits])
        start = pos + 2 + digits
        blocks.append(raw[start : start + length])
        pos = start + length
    return blocks
