"""Siglent SDS3034X HD oscilloscope driver for message-based VISA resources."""

from __future__ import annotations

import math
import struct
import time
from pathlib import Path

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

_MEASUREMENTS = {
    ScopeMeasurementType.VPP: ("PKPK", "V"),
    ScopeMeasurementType.VMAX: ("MAX", "V"),
    ScopeMeasurementType.VMIN: ("MIN", "V"),
    ScopeMeasurementType.VAVG: ("MEAN", "V"),
    ScopeMeasurementType.VRMS: ("RMS", "V"),
    ScopeMeasurementType.FREQUENCY: ("FREQ", "Hz"),
    ScopeMeasurementType.PERIOD: ("PER", "s"),
    ScopeMeasurementType.DUTY_CYCLE: ("DUTY", "%"),
}
_ACQUISITION_MODES = {
    "NORMAL": AcquisitionMode.NORMAL,
    "AVERAGE": AcquisitionMode.AVERAGE,
    "PEAK": AcquisitionMode.PEAK_DETECT,
    "ERES": AcquisitionMode.HIGH_RESOLUTION,
}
_TRIGGER_STATUS = {
    "ARM": TriggerStatus.ARMED,
    "READY": TriggerStatus.READY,
    "AUTO": TriggerStatus.AUTO,
    "TRIG'D": TriggerStatus.TRIGGERED,
    "STOP": TriggerStatus.READY,
    "ROLL": TriggerStatus.SCAN,
}
# WAVEDESC time/div enumeration for the SDS3034X HD (programming guide, table 3).
_TIMEBASE_SECONDS = (200e-12, 500e-12, *(step * 10.0**power for power in range(-9, 3) for step in (1, 2, 5)), 1000.0)


def _block_payload(message: bytes, *, allow_trailing: bool = False) -> memoryview:
    """Read a definite-length block with an optional Siglent command header."""
    start = message.find(b"#")
    digits_byte = message[start + 1 : start + 2]
    if start < 0 or not digits_byte.isdigit() or digits_byte == b"0":
        raise ValueError("Missing Siglent binary block length")
    digits = int(digits_byte)
    payload = start + 2 + digits
    length = message[start + 2 : payload]
    if len(length) != digits or not length.isdigit():
        raise ValueError("Invalid Siglent binary block length")
    end = payload + int(length)
    if end > len(message) or (not allow_trailing and message[end:] not in (b"", b"\n", b"\n\n", b"\r\n")):
        raise ValueError("Incomplete or unexpected Siglent binary block")
    return memoryview(message)[payload:end]


def _instrument_path(name: str) -> str:
    if not name or any(c in name for c in '"\r\n'):
        raise ValueError("Invalid instrument file path")
    return f'"{name}"'


class SiglentSDS3034XHD(ScopeDriverBase):
    """Control the four analog inputs of a Siglent SDS3034X HD over VISA."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        """Connect to the scope through a VISA resource string or a configured ``VisaConfig``.

        Example:
            >>> from instro import InstroScope
            >>> driver = SiglentSDS3034XHD("TCPIP0::scope.local::inst0::INSTR")
            >>> scope = InstroScope("bench", driver=driver, num_channels=4)
        """
        self._visa = VisaDriver(visa_resource)
        self._single_armed = False
        self._continuous_mode = "AUTO"
        self._pending_averages: int | None = None
        self._trigger_source: int | None = None

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        try:
            self._visa.close()
        finally:
            self._continuous_mode = "AUTO"
            self._single_armed = False
            self._pending_averages = None
            self._trigger_source = None

    def check_errors(self) -> None:
        errors = []
        for _ in range(64):
            response = self._visa.query(":SYST:ERR?").strip()
            if int(response.split(",", 1)[0]) == 0:
                break
            errors.append(response)
        else:
            errors.append("Siglent error queue did not drain")
        if errors:
            raise RuntimeError("Siglent SCPI error: " + "; ".join(errors))

    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        self._visa.write(f":CHAN{channel}:SCAL {volts_per_div:g}")

    def get_vertical_scale(self, channel: int) -> float:
        return float(self._visa.query(f":CHAN{channel}:SCAL?"))

    def set_vertical_offset(self, offset: float, channel: int) -> None:
        self._visa.write(f":CHAN{channel}:OFFS {offset:g}")

    def get_vertical_offset(self, channel: int) -> float:
        return float(self._visa.query(f":CHAN{channel}:OFFS?"))

    def set_coupling(self, coupling: Coupling, channel: int) -> None:
        self._visa.write(f":CHAN{channel}:COUP {coupling.value}")

    def get_coupling(self, channel: int) -> Coupling:
        response = self._visa.query(f":CHAN{channel}:COUP?").strip().upper()
        if response == "GND":
            raise NotImplementedError(f"InstroScope cannot represent ground coupling on channel {channel}")
        return Coupling(response)

    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        self._visa.write(f":CHAN{channel}:PROB VAL,{factor:g}")

    def get_probe_attenuation(self, channel: int) -> float:
        return float(self._visa.query(f":CHAN{channel}:PROB?"))

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        self._visa.write(f":TIM:SCAL {seconds_per_div:g}")

    def get_horizontal_scale(self) -> float:
        return float(self._visa.query(":TIM:SCAL?"))

    def get_sample_rate(self) -> float:
        return float(self._visa.query(":ACQ:SRAT?"))

    def set_acquisition_mode(self, mode: AcquisitionMode) -> None:
        if mode is AcquisitionMode.ENVELOPE:
            raise NotImplementedError("SDS3034X HD does not support envelope acquisition")
        with self._visa.lock():
            if mode is AcquisitionMode.AVERAGE:
                self._visa.write(f":ACQ:TYPE AVER,{self.get_average_count()}")
                self.check_errors()
                self._pending_averages = None
            else:
                # This firmware requires an explicit bit count on entry. Keep a front-panel choice on reentry.
                if mode is AcquisitionMode.HIGH_RESOLUTION and self.get_acquisition_mode() is mode:
                    return
                command = {
                    AcquisitionMode.NORMAL: "NORM",
                    AcquisitionMode.PEAK_DETECT: "PEAK",
                    AcquisitionMode.HIGH_RESOLUTION: "ERES,0.5",
                }[mode]
                self._visa.write(f":ACQ:TYPE {command}")

    def get_acquisition_mode(self) -> AcquisitionMode:
        return _ACQUISITION_MODES[self._visa.query(":ACQ:TYPE?").strip().split(",")[0].upper()]

    def set_average_count(self, count: int) -> None:
        if count not in (4, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192):
            raise ValueError(f"Unsupported SDS3034X HD average count: {count}")
        with self._visa.lock():
            if self.get_acquisition_mode() is AcquisitionMode.AVERAGE:
                self._visa.write(f":ACQ:TYPE AVER,{count}")
                self.check_errors()
                self._pending_averages = None
            else:
                # Writing AVGA or ACQ:TYPE AVER also switches the device into averaging mode.
                self._pending_averages = count

    def get_average_count(self) -> int:
        with self._visa.lock():
            if self._pending_averages is not None:
                if self.get_acquisition_mode() is not AcquisitionMode.AVERAGE:
                    return self._pending_averages
                self._pending_averages = None
            return int(self._visa.query("AVGA?").split()[-1])

    def run(self) -> None:
        with self._visa.lock():
            if self._visa.query(":TRIG:MODE?").strip().upper().startswith("SING"):
                self._visa.write(f":TRIG:MODE {self._continuous_mode}")
            else:
                self._visa.write(":TRIG:RUN")
            self._single_armed = False

    def stop(self) -> None:
        with self._visa.lock():
            self._visa.write(":TRIG:STOP")
            self.check_errors()
            self._single_armed = False
            deadline = time.monotonic() + 5
            while self.get_acquisition_state() is not AcquisitionState.STOPPED:
                if time.monotonic() >= deadline:
                    raise TimeoutError("SDS3034X HD did not stop acquisition")
                time.sleep(0.05)
                # A pending run transition can supersede the first STOP on this firmware.
                self._visa.write(":TRIG:STOP")

    def single(self) -> None:
        with self._visa.lock():
            mode = self._visa.query(":TRIG:MODE?").strip().upper()
            if mode in ("AUTO", "NORMAL"):
                self._continuous_mode = mode
            self._visa.write(":TRIG:MODE SING")
            self._visa.query("*OPC?")
            self.check_errors()
            self._single_armed = True

    def digitize(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        # InstroScope calls single() before waiting here. Re-arming would discard a one-shot event.
        if not self._single_armed:
            self.single()
        while time.monotonic() < deadline:
            if self.get_acquisition_state() is AcquisitionState.STOPPED:
                self._single_armed = False
                return
            time.sleep(0.01)
        self.stop()
        raise TimeoutError(f"SDS3034X HD single acquisition timed out after {timeout:g} s")

    def get_acquisition_state(self) -> AcquisitionState:
        status = self._visa.query(":TRIG:STAT?").strip().upper()
        return AcquisitionState.STOPPED if status == "STOP" else AcquisitionState.RUNNING

    def _binary_query(self, command: str) -> bytes:
        """Read to the VISA end-of-message, including embedded and trailing newlines."""
        with self._visa.lock():
            resource = self._visa._inst
            if resource is None:
                raise RuntimeError("SDS3034X HD connection is closed")
            if resource.resource_name.upper().endswith("::SOCKET"):
                raise ValueError("Binary transfers require a message-based VISA resource such as TCPIP::host::INSTR")
            termination = resource.read_termination
            resource.read_termination = None  # type: ignore[assignment]
            try:
                return self._visa.query_raw(command)
            except Exception:
                self._visa.clear()
                raise
            finally:
                resource.read_termination = termination  # type: ignore[assignment]

    def fetch_waveform(self, channel: int) -> WaveformData:
        """Read a stopped, non-segmented acquisition as signed words with trigger-relative timestamps."""
        with self._visa.lock():
            if self.get_acquisition_state() is not AcquisitionState.STOPPED:
                raise RuntimeError("Stop acquisition or call single() before fetching a waveform")
            if self._visa.query(":ACQ:SEQ?").strip().upper() == "ON":
                raise NotImplementedError("Segmented acquisitions have a separate time axis for each frame")
            if self._visa.query(f":CHAN{channel}:SWIT?").strip().upper() != "ON":
                raise ValueError(f"Channel {channel} is disabled")
            for command in (
                f":WAV:SOUR C{channel}",
                ":WAV:WIDT WORD",
                ":WAV:BYT LSB",
                ":WAV:STAR 0",
                ":WAV:POIN 0",
                ":WAV:INT 1",
            ):
                self._visa.write(command)
            self.check_errors()
            desc = _block_payload(self._binary_query(":WAV:PRE?"))
            if len(desc) < 346 or desc[:8] != b"WAVEDESC" or struct.unpack_from("<hh", desc, 32) != (1, 0):
                raise ValueError("Expected a little-endian WORD WAVEDESC")
            points = struct.unpack_from("<i", desc, 116)[0]
            if points <= 0 or struct.unpack_from("<h", desc, 344)[0] != channel - 1:
                raise ValueError(f"No valid waveform descriptor for channel {channel}")
            gain, offset, codes_per_div = struct.unpack_from("<fff", desc, 156)
            interval, delay = struct.unpack_from("<fd", desc, 176)
            probe = struct.unpack_from("<f", desc, 328)[0]
            if (
                not all(math.isfinite(value) for value in (gain, offset, codes_per_div, interval, delay, probe))
                or gain == 0
                or min(codes_per_div, interval, probe) <= 0
            ):
                raise ValueError("Invalid Siglent WAVEDESC voltage or time scaling")
            step_volts = gain * probe / codes_per_div
            offset_volts = offset * probe
            timebase_index = struct.unpack_from("<h", desc, 324)[0]
            if not 0 <= timebase_index < len(_TIMEBASE_SECONDS):
                raise ValueError(f"Unknown Siglent timebase index {timebase_index}")
            origin = delay - 5 * _TIMEBASE_SECONDS[timebase_index]
            chunk = min(int(self._visa.query(":WAV:MAXP?")), 1_000_000)
            if chunk <= 0:
                raise ValueError("Invalid Siglent waveform transfer limit")
            voltages: list[float] = []
            for start in range(0, points, chunk):
                count = min(points - start, chunk)
                self._visa.write(f":WAV:POIN {count}")
                self._visa.write(f":WAV:STAR {start}")
                self.check_errors()
                data = _block_payload(self._binary_query(":WAV:DATA?"))
                if len(data) != count * 2:
                    raise ValueError(f"Expected {count} waveform samples at {start}, received {len(data)} bytes")
                voltages.extend(code * step_volts - offset_volts for (code,) in struct.iter_unpack("<h", data))
        return WaveformData(times=[round((origin + i * interval) * 1e9) for i in range(points)], voltages=voltages)

    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        """Query the channel directly without replacing a front-panel measurement slot."""
        item, unit = _MEASUREMENTS[measurement_type]
        response = self._visa.query(f"C{channel}:PAVA? {item}")
        token = response.strip().rsplit(",", 1)[-1].removesuffix(unit)
        try:
            value = float(token)
        except ValueError:
            return math.nan
        return value if abs(value) < 9.9e37 else math.nan

    def _trigger_group(self) -> str:
        group = self._visa.query(":TRIG:TYPE?").strip().upper()
        if group not in ("EDGE", "PULSE"):
            raise NotImplementedError(
                f"Select an edge or pulse trigger before configuring its source or level ({group})"
            )
        return group

    def set_trigger_source(self, channel: int) -> None:
        with self._visa.lock():
            self._visa.write(f":TRIG:{self._trigger_group()}:SOUR C{channel}")
            self.check_errors()
            self._trigger_source = channel

    def set_trigger_type(self, trigger_type: TriggerType) -> None:
        with self._visa.lock():
            if self._trigger_source is not None:
                # Each trigger type has its own source. A front-panel edit supersedes our last selection.
                group = self._visa.query(":TRIG:TYPE?").strip().upper()
                if group not in ("EDGE", "PULSE") or (
                    self._visa.query(f":TRIG:{group}:SOUR?").strip().upper() != f"C{self._trigger_source}"
                ):
                    self._trigger_source = None
            self._visa.write(f":TRIG:TYPE {trigger_type.value}")
            if self._trigger_source is not None:
                self._visa.write(f":TRIG:{trigger_type.value}:SOUR C{self._trigger_source}")

    def set_trigger_level(self, level: float) -> None:
        with self._visa.lock():
            self._visa.write(f":TRIG:{self._trigger_group()}:LEV {level:g}")

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        with self._visa.lock():
            if self._trigger_group() != "EDGE":
                raise NotImplementedError("Edge slope requires an edge trigger")
            token = {TriggerSlope.RISING: "RIS", TriggerSlope.FALLING: "FALL", TriggerSlope.EITHER: "ALT"}[slope]
            self._visa.write(f":TRIG:EDGE:SLOP {token}")

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        self._visa.write(f":TRIG:MODE {mode.value}")
        self._single_armed = False

    def force_trigger(self) -> None:
        # The HD temporarily enters FTRIG. Commands sent during that transition can be ignored.
        with self._visa.lock():
            single = self._visa.query(":TRIG:MODE?").strip().upper().startswith("SING")
            self._visa.write(":TRIG:MODE FTRIG")
            self.check_errors()
            deadline = time.monotonic() + 15
            while self._visa.query(":TRIG:MODE?").strip().upper() == "FTRIG" or (
                single and self.get_acquisition_state() is not AcquisitionState.STOPPED
            ):
                if time.monotonic() >= deadline:
                    raise TimeoutError("SDS3034X HD did not finish the forced trigger")
                time.sleep(0.02)

    def get_trigger_status(self) -> TriggerStatus:
        return _TRIGGER_STATUS[self._visa.query(":TRIG:STAT?").strip().upper()]

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        """Save a PNG on the host or the instrument."""
        if to_instrument:
            self._visa.write(f":SAVE:IMAG {_instrument_path(filepath)},PNG,OFF")
            return b""
        data = self._binary_query(":PRIN? PNG")
        iend = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
        end = data.rfind(iend) + len(iend)
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or end < len(iend) or data[end:].strip(b"\r\n"):
            raise ValueError("Incomplete Siglent PNG screenshot")
        data = data[:end]
        Path(filepath).write_bytes(data)
        return data

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        if to_instrument:
            self._visa.write(f":SAVE:SET EXT,{_instrument_path(name)}")
            return b""
        # After a setup recall this firmware can append stale bytes beyond the declared XML length.
        data = bytes(_block_payload(self._binary_query("PNSU?"), allow_trailing=True))
        Path(name).write_bytes(data)
        return data

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        with self._visa.lock():
            if from_instrument:
                self._visa.write(f":REC:SET EXT,{_instrument_path(name)}")
            else:
                data = Path(name).read_bytes()
                self._visa.write_raw(f"PNSU #9{len(data):09d}".encode() + data + b"\n")
            self._pending_averages = None
            self._trigger_source = None
            self._single_armed = False
            self._continuous_mode = "AUTO"
            self._visa.query("*OPC?")
