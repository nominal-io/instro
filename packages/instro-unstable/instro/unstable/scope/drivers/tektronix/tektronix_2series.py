"""Tektronix 2 Series MSO oscilloscope driver (MSO22, MSO24)."""

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


class Tektronix2SeriesMSO(ScopeDriverBase):
    """SCPI driver for Tektronix 2 Series MSO oscilloscopes (MSO22, MSO24)."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._trigger_source: int | None = None
        self._measurement_slots: dict[tuple[ScopeMeasurementType, int], str] = {}

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def check_errors(self) -> None:
        """Drain ``ALLEv?`` and raise on any event with code > 1."""
        resp = self._visa.query("ALLEv?")
        # ALLEv? returns "1" when no events, or event codes with messages
        if resp.strip() == "1":
            return
        # Check for actual errors (codes 100-999 are errors)
        events = resp.split(";")
        errors = []
        for event in events:
            event = event.strip()
            if not event:
                continue
            try:
                code = int(event.split(",")[0])
                if code > 1:
                    errors.append(event)
            except (ValueError, IndexError):
                continue
        if errors:
            raise RuntimeError(f"Tektronix SCPI errors: {'; '.join(errors)}")

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
        return Coupling.DC

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
        """Fetch the waveform from ``channel`` over ``RIBinary`` (signed 16-bit)."""
        self._visa.write(f"DATa:SOUrce CH{channel}")
        self._visa.write("DATa:ENCdg RIBinary")
        self._visa.write("WFMOutpre:BYT_Nr 2")  # each point returns a signed 2 byte integer
        self._visa.write("DATa:STARt 1")
        # Check errors before querying data — if any setup command failed,
        # the data query would hang waiting for a response that won't come.
        self.check_errors()

        nr_pt = int(float(self._visa.query("WFMOutpre:NR_Pt?")))  # points per record
        self._visa.write(f"DATa:STOP {nr_pt}")

        x_incr = float(self._visa.query("WFMOutpre:XINcr?"))  # period time, seconds, float
        x_zero = float(self._visa.query("WFMOutpre:XZEro?"))  # offset time from trigger to first sample
        y_mult = float(self._visa.query("WFMOutpre:YMUlt?"))  # scaling factor to apply to each data point
        y_off = float(self._visa.query("WFMOutpre:YOFf?"))  # will always be zero for MS0 devices, per manual
        y_zero = float(self._visa.query("WFMOutpre:YZEro?"))  # the vertical position offset from the scope

        x_incr_ns = int(x_incr * 1e9)  # convert to nanoseconds integer, for greater compatibility with library
        x_zero_ns = int(x_zero * 1e9)

        points = self._visa.query_binary_values("CURVe?", datatype="h", is_big_endian=True, container=list)

        times = [x_zero_ns + i * x_incr_ns for i in range(nr_pt)]
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
        """``FILESystem:WRITEFile`` to the scope's filesystem."""
        self._visa.write_raw(f'FILESystem:WRITEFile "{instrument_path}",'.encode() + data)

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        if to_instrument:
            self._visa.write(f'SAVe:IMAGe "{filepath}"')
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
        self._visa.write(f'FILESystem:DELete "{temp_path}"')
