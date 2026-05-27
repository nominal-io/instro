"""In-process SMU SCPI emulator (TCP socket transport).

Internal physical model (CV/CC/compliance switching, probe resistance, EMF
on load) drives realistic responses to the standard SCPI surface — no
simulator-only extensions.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import socket
import threading
import time
from collections import deque
from enum import Enum
from typing import Any, Callable

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Log, Static

logger = logging.getLogger(__name__)

DEFAULT_PORT = 5025
DEFAULT_NUM_CHANNELS = 2
DEFAULT_LOAD_RESISTANCE = 1000.0  # ohms
DEFAULT_PROBE_RESISTANCE = 10.0  # ohms


def add_noise(value: float, percent: float) -> float:
    if not math.isfinite(value):
        return value
    std_dev = abs(value) * percent / 3
    return random.gauss(value, std_dev)


class SCPIError(Enum):
    """SCPI error table — standard SCPI errors only."""

    NO_ERROR = 0
    # Command errors (-100 to -178)
    COMMAND_ERROR = -100
    INVALID_CHARACTER = -101
    SYNTAX_ERROR = -102
    INVALID_SEPARATOR = -103
    DATA_TYPE_ERROR = -104
    PARAMETER_NOT_ALLOWED = -108
    MISSING_PARAMETER = -109
    UNDEFINED_HEADER = -113
    HEADER_SUFFIX_OUT_OF_RANGE = -114
    INVALID_SUFFIX = -131
    SUFFIX_NOT_ALLOWED = -138
    INVALID_CHARACTER_DATA = -141
    # Execution errors (-200 to -241)
    EXECUTION_ERROR = -200
    SETTINGS_CONFLICT = -221
    DATA_OUT_OF_RANGE = -222
    ILLEGAL_PARAMETER_VALUE = -224
    HARDWARE_MISSING = -241
    # Device-dependent errors (-300 to -350)
    DEVICE_SPECIFIC_ERROR = -300
    SYSTEM_ERROR = -310
    QUEUE_OVERFLOW = -350
    # Query errors (-400 to -440)
    QUERY_ERROR = -400

    @property
    def message(self) -> str:
        return self.name.replace("_", " ").lower().capitalize()


class OperatingMode(Enum):
    OFF = "OFF"
    CV = "CV"  # voltage regulated
    CC = "CC"  # current regulated (compliance reached when in voltage-source mode)


class SourceMode(Enum):
    VOLTAGE = "VOLT"
    CURRENT = "CURR"


class SimulatedLoad:
    """Series chain attached to a channel: probe leads → load resistor + optional EMF."""

    def __init__(
        self,
        resistance: float = DEFAULT_LOAD_RESISTANCE,
        emf: float = 0.0,
        probe_resistance: float = DEFAULT_PROBE_RESISTANCE,
    ) -> None:
        self.resistance = resistance
        self.emf = emf
        self.probe_resistance = probe_resistance


class SimulatedPSUChannel:
    """Per-channel state — setpoints, compliance limits, sense mode, observed values."""

    def __init__(
        self,
        channel_id: int,
        load: SimulatedLoad | None = None,
    ) -> None:
        self.channel_id = channel_id
        # Source side
        self.source_mode = SourceMode.VOLTAGE
        self.voltage_setpoint = 0.0
        self.current_setpoint = 0.0
        # Compliance (sense-side protection). Symmetric defaults; positive/negative
        # tracked separately to allow asymmetric limits per B2900.
        self.voltage_compliance = math.inf
        self.current_compliance = math.inf
        # Protection latch enable (OUTPut:PROTection[:STATe]). When True, hitting
        # compliance turns output off automatically and immediately.
        self.protection_enabled = False
        # Remote (4-wire) sense enable.
        self.remote_sense = False
        self.output_enabled = False
        self.load = load if load is not None else SimulatedLoad()
        # Observed / measured state
        self.terminal_voltage = 0.0
        self.load_voltage = 0.0
        self.current = 0.0
        self.mode = OperatingMode.OFF
        self.voltage_compliance_tripped = False
        self.current_compliance_tripped = False
        self.protection_latched = False  # True after OUTP:PROT auto-shutdown


# --- SCPI normalization ---

# SCPI keyword long form → short form. Used to canonicalize header parts so
# `:OUTPut:PROTection:STATe` and `:OUTP:PROT:STAT` both dispatch the same way.
_SHORT_FORMS: dict[str, str] = {
    "OUTPUT": "OUTP",
    "STATE": "STAT",
    "PROTECTION": "PROT",
    "SOURCE": "SOUR",
    "VOLTAGE": "VOLT",
    "CURRENT": "CURR",
    "LEVEL": "LEV",
    "IMMEDIATE": "IMM",
    "AMPLITUDE": "AMPL",
    "SENSE": "SENS",
    "MEASURE": "MEAS",
    "SYSTEM": "SYST",
    "ERROR": "ERR",
    "REMOTE": "REM",
    "FUNCTION": "FUNC",
    "TRIPPED": "TRIP",
    "POSITIVE": "POS",
    "NEGATIVE": "NEG",
}


def _normalize_part(part: str) -> tuple[str, int | None]:
    """Canonicalize one SCPI header part to its short form and pull off any trailing channel suffix."""
    upper = part.upper()
    i = len(upper)
    while i > 0 and upper[i - 1].isdigit():
        i -= 1
    base = upper[:i]
    suffix_str = upper[i:]
    suffix = int(suffix_str) if suffix_str else None
    canonical = _SHORT_FORMS.get(base, base)
    return canonical, suffix


def _normalize_header(header: str) -> tuple[str, int]:
    """Parse a SCPI header. Returns (canonical_header, channel).

    Numeric suffix on any path component is treated as the channel number.
    The first numeric suffix wins; default is channel 1 if none present.
    Strips an optional leading colon.
    """
    if header.startswith(":"):
        header = header[1:]
    channel = 1
    canonical_parts: list[str] = []
    for raw in header.split(":"):
        canonical, suffix = _normalize_part(raw)
        if suffix is not None:
            channel = suffix
        canonical_parts.append(canonical)
    return ":".join(canonical_parts), channel


class SimulatedPSU:
    """Emulates a source measurement unit — two independent SMU outputs in voltage-source mode."""

    id = "NOMINAL,SIMULATED_PSU,000001,1.0"

    def __init__(
        self,
        num_channels: int = DEFAULT_NUM_CHANNELS,
        channels: list[SimulatedPSUChannel] | None = None,
    ) -> None:
        if channels is not None:
            self.channels: list[SimulatedPSUChannel] = channels
        else:
            self.channels = [SimulatedPSUChannel(i) for i in range(1, num_channels + 1)]
        self._error_queue: deque[int] = deque()
        # Rolling SCPI command log for the TUI. Monotonic counter lets the
        # log panel write only new entries on each refresh tick.
        self._command_log: deque[str] = deque(maxlen=200)
        self._command_log_seq = 0

    # ---- Channel lookup and error queue ----

    def _channel(self, channel_id: int) -> SimulatedPSUChannel | None:
        for ch in self.channels:
            if ch.channel_id == channel_id:
                return ch
        return None

    def _push_error(self, err: SCPIError) -> None:
        self._error_queue.append(err.value)

    # ---- Top-level dispatch ----

    def process_scpi_command(self, cmd: str) -> Any:
        stripped = cmd.strip()
        if not stripped:
            return None
        errors_before = len(self._error_queue)
        response = self._dispatch(stripped)
        self._record_log(stripped, response, errors_before)
        return response

    def _dispatch(self, cmd: str) -> Any:
        header_raw, _, rest = cmd.partition(" ")
        rest = rest.strip()

        is_query = header_raw.endswith("?")
        if is_query:
            header_raw = header_raw[:-1]

        canonical, channel = _normalize_header(header_raw)

        key = canonical + ("?" if is_query else "")
        handler = _COMMAND_TABLE.get(key)
        if handler is None:
            logger.error("Unknown command: %s", cmd)
            self._push_error(SCPIError.UNDEFINED_HEADER)
            return None

        positional = [a.strip() for a in rest.split(",") if a.strip()] if rest else []
        logger.info("Cmd %s channel=%d args=%s", key, channel, positional)
        try:
            return handler(self, channel, positional)
        except ValueError:
            logger.warning("Invalid parameter in command: %s", cmd)
            self._push_error(SCPIError.INVALID_CHARACTER_DATA)
            return None
        except Exception:
            logger.exception("Unhandled error processing command: %s", cmd)
            self._push_error(SCPIError.DEVICE_SPECIFIC_ERROR)
            return None

    def _record_log(self, cmd: str, response: Any, errors_before: int) -> None:
        parts = [time.strftime("%H:%M:%S"), cmd]
        if response is not None:
            resp_text = str(response)
            if len(resp_text) > 60:
                resp_text = resp_text[:57] + "..."
            parts.append(f"-> {resp_text}")
        for code in list(self._error_queue)[errors_before:]:
            try:
                err = SCPIError(code)
            except ValueError:
                err = SCPIError.DEVICE_SPECIFIC_ERROR
            parts.append(f"! {code:+d} {err.message}")
        self._command_log.append("  ".join(parts))
        self._command_log_seq += 1

    # ---- *IDN? and SYST:ERR? ----

    def _get_id(self, channel: int, args: list[str]) -> str:
        time.sleep(0.015)
        return self.id

    def _get_error(self, channel: int, args: list[str]) -> str:
        code = self._error_queue.popleft() if self._error_queue else SCPIError.NO_ERROR.value
        try:
            err = SCPIError(code)
        except ValueError:
            err = SCPIError.DEVICE_SPECIFIC_ERROR
        return f'{code:+d},"{err.message}"'

    def _reset(self, channel: int, args: list[str]) -> None:
        for ch in self.channels:
            ch.__init__(ch.channel_id, ch.load)  # type: ignore[misc]
        self._error_queue.clear()

    def _clear_status(self, channel: int, args: list[str]) -> None:
        self._error_queue.clear()

    # ---- SOURce subsystem ----

    def _set_source_mode(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        token = args[0].upper()
        if token in ("VOLT", "VOLTAGE"):
            ch.source_mode = SourceMode.VOLTAGE
        elif token in ("CURR", "CURRENT"):
            ch.source_mode = SourceMode.CURRENT
        else:
            self._push_error(SCPIError.ILLEGAL_PARAMETER_VALUE)
            return
        self._update()

    def _query_source_mode(self, channel: int, args: list[str]) -> str:
        ch = self._require_channel(channel)
        if ch is None:
            return ""
        return ch.source_mode.value

    def _set_voltage(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        ch.voltage_setpoint = float(args[0])
        self._update()

    def _query_voltage(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        return ch.voltage_setpoint if ch else 0.0

    def _set_current(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        ch.current_setpoint = float(args[0])
        self._update()

    def _query_current(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        return ch.current_setpoint if ch else 0.0

    # ---- OUTPut subsystem ----

    def _set_output(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        enable = self._parse_bool(args[0])
        if enable is None:
            return
        if enable and ch.protection_latched:
            self._push_error(SCPIError.SETTINGS_CONFLICT)
            return
        ch.output_enabled = enable
        self._update()

    def _query_output(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.output_enabled) else 0

    def _set_output_protection(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        enable = self._parse_bool(args[0])
        if enable is None:
            return
        ch.protection_enabled = enable
        self._update()

    def _query_output_protection(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.protection_enabled) else 0

    def _clear_protection_latch(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None:
            return
        ch.protection_latched = False

    # ---- SENSe subsystem ----

    def _set_voltage_compliance(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        ch.voltage_compliance = self._parse_limit(args[0])
        self._update()

    def _query_voltage_compliance(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        return ch.voltage_compliance if ch else 0.0

    def _query_voltage_compliance_tripped(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.voltage_compliance_tripped) else 0

    def _set_current_compliance(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        ch.current_compliance = self._parse_limit(args[0])
        self._update()

    def _query_current_compliance(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        return ch.current_compliance if ch else 0.0

    def _query_current_compliance_tripped(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.current_compliance_tripped) else 0

    def _set_remote_sense(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        enable = self._parse_bool(args[0])
        if enable is None:
            return
        ch.remote_sense = enable
        self._update()

    def _query_remote_sense(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.remote_sense) else 0

    # ---- MEASure subsystem ----

    def _measure_voltage(self, channel: int, args: list[str]) -> float:
        self._update()
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        observed = ch.load_voltage if ch.remote_sense else ch.terminal_voltage
        return add_noise(observed, 0.005)

    def _measure_current(self, channel: int, args: list[str]) -> float:
        self._update()
        ch = self._require_channel(channel)
        return add_noise(ch.current, 0.005) if ch else 0.0

    # ---- Helpers ----

    def _require_channel(self, channel: int) -> SimulatedPSUChannel | None:
        ch = self._channel(channel)
        if ch is None:
            self._push_error(SCPIError.HEADER_SUFFIX_OUT_OF_RANGE)
        return ch

    def _require_args(self, args: list[str]) -> bool:
        if not args:
            self._push_error(SCPIError.MISSING_PARAMETER)
            return False
        return True

    def _parse_bool(self, token: str) -> bool | None:
        upper = token.upper()
        if upper in ("1", "ON"):
            return True
        if upper in ("0", "OFF"):
            return False
        self._push_error(SCPIError.ILLEGAL_PARAMETER_VALUE)
        return None

    def _parse_limit(self, token: str) -> float:
        upper = token.upper()
        if upper in ("MAX", "MAXIMUM"):
            return math.inf
        if upper in ("MIN", "MINIMUM", "DEF", "DEFAULT"):
            return 0.0
        return float(token)

    # ---- Physics ----

    def _update(self) -> None:
        for ch in self.channels:
            self._update_channel(ch)

    def _update_channel(self, ch: SimulatedPSUChannel) -> None:
        if not ch.output_enabled or ch.protection_latched:
            ch.terminal_voltage = 0.0
            ch.load_voltage = 0.0
            ch.current = 0.0
            ch.mode = OperatingMode.OFF
            ch.voltage_compliance_tripped = False
            ch.current_compliance_tripped = False
            return

        if ch.source_mode == SourceMode.VOLTAGE:
            self._update_voltage_source(ch)
        else:
            self._update_current_source(ch)

        # Output protection: if enabled and any compliance reached, latch output off.
        if ch.protection_enabled and (ch.current_compliance_tripped or ch.voltage_compliance_tripped):
            ch.protection_latched = True
            ch.output_enabled = False
            ch.terminal_voltage = 0.0
            ch.load_voltage = 0.0
            ch.current = 0.0
            ch.mode = OperatingMode.OFF

    def _update_voltage_source(self, ch: SimulatedPSUChannel) -> None:
        v_set = ch.voltage_setpoint
        i_limit = ch.current_compliance
        r_load = ch.load.resistance
        r_probe = ch.load.probe_resistance
        emf = ch.load.emf

        r_total = r_load if ch.remote_sense else r_load + r_probe

        if r_total == 0:
            i_demand = math.inf if (v_set - emf) != 0 else 0.0
        elif not math.isfinite(r_total):
            i_demand = 0.0
        else:
            i_demand = (v_set - emf) / r_total

        if abs(i_demand) <= i_limit:
            ch.mode = OperatingMode.CV
            ch.current = i_demand
            if ch.remote_sense:
                ch.load_voltage = v_set
                ch.terminal_voltage = v_set + i_demand * r_probe
            else:
                ch.terminal_voltage = v_set
                ch.load_voltage = v_set - i_demand * r_probe
            ch.current_compliance_tripped = False
        else:
            ch.mode = OperatingMode.CC
            ch.current = i_limit if i_demand > 0 else -i_limit
            if math.isfinite(r_load):
                ch.load_voltage = ch.current * r_load + emf
                ch.terminal_voltage = ch.load_voltage + ch.current * r_probe
            else:
                ch.load_voltage = 0.0
                ch.terminal_voltage = 0.0
            ch.current_compliance_tripped = True

        ch.voltage_compliance_tripped = abs(ch.terminal_voltage) > ch.voltage_compliance

    def _update_current_source(self, ch: SimulatedPSUChannel) -> None:
        i_set = ch.current_setpoint
        v_limit = ch.voltage_compliance
        r_load = ch.load.resistance
        r_probe = ch.load.probe_resistance
        emf = ch.load.emf
        r_total = r_load + r_probe

        if not math.isfinite(r_load):
            v_demand = math.copysign(math.inf, i_set) if i_set != 0 else emf
        else:
            v_demand = i_set * r_total + emf

        if abs(v_demand) <= v_limit:
            ch.mode = OperatingMode.CC
            ch.current = i_set
            if math.isfinite(r_load):
                ch.load_voltage = i_set * r_load + emf
                ch.terminal_voltage = ch.load_voltage + i_set * r_probe
            else:
                ch.load_voltage = 0.0
                ch.terminal_voltage = 0.0
            ch.voltage_compliance_tripped = False
        else:
            ch.mode = OperatingMode.CV
            v_terminal = v_limit if v_demand > 0 else -v_limit
            ch.terminal_voltage = v_terminal
            if math.isfinite(r_total) and r_total > 0:
                ch.current = (v_terminal - emf) / r_total
                ch.load_voltage = ch.current * r_load + emf if math.isfinite(r_load) else v_terminal
            else:
                ch.current = 0.0
                ch.load_voltage = v_terminal
            ch.voltage_compliance_tripped = True

        ch.current_compliance_tripped = abs(ch.current) > ch.current_compliance


# ---- Command dispatch table (keyed on canonical SCPI short-form header + optional ?) ----

_COMMAND_TABLE: dict[str, Callable[..., Any]] = {
    "*IDN?": SimulatedPSU._get_id,
    "*RST": SimulatedPSU._reset,
    "*CLS": SimulatedPSU._clear_status,
    "SYST:ERR?": SimulatedPSU._get_error,
    "SYST:ERR:NEXT?": SimulatedPSU._get_error,
    "SOUR:FUNC:MODE": SimulatedPSU._set_source_mode,
    "SOUR:FUNC:MODE?": SimulatedPSU._query_source_mode,
    # [:SOURce] is an optional prefix on the B2900 — accept bare forms too.
    "FUNC:MODE": SimulatedPSU._set_source_mode,
    "FUNC:MODE?": SimulatedPSU._query_source_mode,
    "VOLT": SimulatedPSU._set_voltage,
    "VOLT?": SimulatedPSU._query_voltage,
    "CURR": SimulatedPSU._set_current,
    "CURR?": SimulatedPSU._query_current,
    "SOUR:VOLT": SimulatedPSU._set_voltage,
    "SOUR:VOLT?": SimulatedPSU._query_voltage,
    "SOUR:VOLT:LEV": SimulatedPSU._set_voltage,
    "SOUR:VOLT:LEV?": SimulatedPSU._query_voltage,
    "SOUR:VOLT:LEV:IMM:AMPL": SimulatedPSU._set_voltage,
    "SOUR:VOLT:LEV:IMM:AMPL?": SimulatedPSU._query_voltage,
    "SOUR:CURR": SimulatedPSU._set_current,
    "SOUR:CURR?": SimulatedPSU._query_current,
    "SOUR:CURR:LEV": SimulatedPSU._set_current,
    "SOUR:CURR:LEV?": SimulatedPSU._query_current,
    "OUTP": SimulatedPSU._set_output,
    "OUTP?": SimulatedPSU._query_output,
    "OUTP:STAT": SimulatedPSU._set_output,
    "OUTP:STAT?": SimulatedPSU._query_output,
    "OUTP:PROT": SimulatedPSU._set_output_protection,
    "OUTP:PROT?": SimulatedPSU._query_output_protection,
    "OUTP:PROT:STAT": SimulatedPSU._set_output_protection,
    "OUTP:PROT:STAT?": SimulatedPSU._query_output_protection,
    "OUTP:PROT:CLE": SimulatedPSU._clear_protection_latch,
    "SENS:VOLT:PROT": SimulatedPSU._set_voltage_compliance,
    "SENS:VOLT:PROT?": SimulatedPSU._query_voltage_compliance,
    "SENS:VOLT:PROT:LEV": SimulatedPSU._set_voltage_compliance,
    "SENS:VOLT:PROT:LEV?": SimulatedPSU._query_voltage_compliance,
    "SENS:VOLT:DC:PROT": SimulatedPSU._set_voltage_compliance,
    "SENS:VOLT:DC:PROT?": SimulatedPSU._query_voltage_compliance,
    "SENS:VOLT:PROT:TRIP?": SimulatedPSU._query_voltage_compliance_tripped,
    "SENS:VOLT:DC:PROT:TRIP?": SimulatedPSU._query_voltage_compliance_tripped,
    "SENS:CURR:PROT": SimulatedPSU._set_current_compliance,
    "SENS:CURR:PROT?": SimulatedPSU._query_current_compliance,
    "SENS:CURR:PROT:LEV": SimulatedPSU._set_current_compliance,
    "SENS:CURR:PROT:LEV?": SimulatedPSU._query_current_compliance,
    "SENS:CURR:DC:PROT": SimulatedPSU._set_current_compliance,
    "SENS:CURR:DC:PROT?": SimulatedPSU._query_current_compliance,
    "SENS:CURR:PROT:TRIP?": SimulatedPSU._query_current_compliance_tripped,
    "SENS:CURR:DC:PROT:TRIP?": SimulatedPSU._query_current_compliance_tripped,
    "SENS:REM": SimulatedPSU._set_remote_sense,
    "SENS:REM?": SimulatedPSU._query_remote_sense,
    "MEAS:VOLT?": SimulatedPSU._measure_voltage,
    "MEAS:VOLT:DC?": SimulatedPSU._measure_voltage,
    "MEAS:CURR?": SimulatedPSU._measure_current,
    "MEAS:CURR:DC?": SimulatedPSU._measure_current,
}


# ---- Background TCP server ----


class SimulatedPSUServer:
    """TCP socket server that hands incoming SCPI lines to the simulator.

    Runs in a daemon thread so the foreground console can keep accepting input.
    A single lock guards every access to the simulator state, so console writes
    and SCPI reads/writes don't tear each other.
    """

    def __init__(self, psu: SimulatedPSU, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.psu = psu
        self._host = host
        self._port = port
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def start(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self._host, self._port))
        self._socket.listen(1)
        self._socket.settimeout(0.5)
        self._thread = threading.Thread(target=self._run, daemon=True, name="psu-sim-server")
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self._handle_client(conn)
            except Exception:
                logger.exception("client handler error")
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(0.5)
        buffer = b""
        while not self._stop.is_set():
            try:
                data = conn.recv(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                cmd_text = line.decode(errors="replace").strip()
                if not cmd_text:
                    continue
                with self.lock:
                    response = self.psu.process_scpi_command(cmd_text)
                if response is not None:
                    try:
                        conn.sendall((str(response) + "\n").encode())
                    except OSError:
                        return


# ---- Interactive TUI ----


def _fmt_limit(value: float) -> str:
    if not math.isfinite(value):
        return "max" if value > 0 else "-max"
    return f"{value:.3f}"


class _PromptScreen(ModalScreen[str | None]):
    """Modal screen that prompts for a single text value."""

    DEFAULT_CSS = """
    _PromptScreen {
        align: center middle;
    }
    _PromptScreen > Vertical {
        background: $surface;
        border: round $primary;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    _PromptScreen Label {
        margin-bottom: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, initial: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._initial)

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ChannelPanel(Container):
    """Per-channel panel: live status info on top, editable actions list below."""

    DEFAULT_CSS = """
    _ChannelPanel {
        border: round $primary;
        padding: 0 1;
        margin: 0 0 1 0;
        height: auto;
    }
    _ChannelPanel > Static {
        height: auto;
    }
    _ChannelPanel > ListView {
        height: auto;
        margin-top: 1;
        background: transparent;
    }
    """

    def __init__(self, server: SimulatedPSUServer, channel_id: int) -> None:
        super().__init__(id=f"ch-{channel_id}")
        self._server = server
        self._channel_id = channel_id
        self.border_title = f"Channel {channel_id}"

    @property
    def channel_id(self) -> int:
        return self._channel_id

    def compose(self) -> ComposeResult:
        yield Static(id="info")
        yield ListView(
            ListItem(Label("Set load R"), id="load"),
            ListItem(Label("Set EMF"), id="emf"),
            ListItem(Label("Set probe R"), id="probe"),
            ListItem(Label("Remove channel"), id="remove"),
            id=f"ch-{self._channel_id}-actions",
        )

    def refresh_state(self) -> None:
        with self._server.lock:
            ch = self._server.psu._channel(self._channel_id)
            if ch is None:
                self.query_one("#info", Static).update("(removed)")
                return
            self._server.psu._update()
            tripped: list[str] = []
            if ch.protection_latched:
                tripped.append("LATCHED")
            if ch.current_compliance_tripped:
                tripped.append("Icomp")
            if ch.voltage_compliance_tripped:
                tripped.append("Vcomp")
            sense_label = "EXT (4-wire)" if ch.remote_sense else "INT (2-wire)"
            source_label = "voltage" if ch.source_mode == SourceMode.VOLTAGE else "current"
            text = (
                f"Mode: {ch.mode.value}   Source: {source_label}   "
                f"Output: {'on' if ch.output_enabled else 'off'}   "
                f"Sense: {sense_label}   Tripped: {', '.join(tripped) or '-'}\n"
                f"\n"
                f"Voltage:  set {ch.voltage_setpoint:.3f} V   "
                f"meas {ch.terminal_voltage:.3f} V   "
                f"V limit {_fmt_limit(ch.voltage_compliance)}\n"
                f"Current:  set {ch.current_setpoint:.3f} A   "
                f"meas {ch.current:.3f} A   "
                f"I limit {_fmt_limit(ch.current_compliance)}\n"
                f"\n"
                f"Load: R={ch.load.resistance} ohm   EMF={ch.load.emf} V   Probe={ch.load.probe_resistance} ohm"
            )
        self.query_one("#info", Static).update(text)


class _PsuPanel(Static):
    """Top-level PSU info panel: identifier + error queue."""

    DEFAULT_CSS = """
    _PsuPanel {
        border: round $accent;
        padding: 0 1;
        margin: 0 0 1 0;
        height: auto;
    }
    """

    def __init__(self, server: SimulatedPSUServer) -> None:
        super().__init__()
        self._server = server
        self.border_title = "PSU"

    def refresh_state(self) -> None:
        with self._server.lock:
            psu_id = self._server.psu.id
        resource = f"TCPIP0::{self._server._host}::{self._server._port}::SOCKET"
        self.update(f"ID:            {psu_id}\nVISA Resource: {resource}")


_ACTIONS_LIST_SUFFIX = "-actions"


class _LogPanel(Log):
    """Scrolling log of SCPI commands, responses, and errors as they arrive."""

    DEFAULT_CSS = """
    _LogPanel {
        border: round $accent;
        height: 12;
        background: transparent;
    }
    """

    def __init__(self, server: SimulatedPSUServer) -> None:
        super().__init__(highlight=False, max_lines=500, auto_scroll=True)
        self._server = server
        self._last_seq = 0
        self.border_title = "SCPI log"

    def refresh_state(self) -> None:
        with self._server.lock:
            current_seq = self._server.psu._command_log_seq
            entries = list(self._server.psu._command_log)
        delta = current_seq - self._last_seq
        if delta <= 0:
            return
        new = entries[-delta:] if delta < len(entries) else entries
        for line in new:
            self.write_line(line)
        self._last_seq = current_seq


class _AddChannelPanel(Container):
    """Bordered panel matching the channel-panel style, holding the '+ Add channel' action."""

    DEFAULT_CSS = """
    _AddChannelPanel {
        border: round $primary;
        padding: 0 1;
        margin: 0 0 1 0;
        height: auto;
    }
    _AddChannelPanel > ListView {
        height: auto;
        background: transparent;
    }
    """

    def compose(self) -> ComposeResult:
        yield ListView(
            ListItem(Label("+ Add channel"), id="add"),
            id="add-channel",
        )


class SimulatedPSUApp(App[None]):
    """Textual app: PSU panel on top, channels stacked vertically with per-channel actions, '+ Add channel' at the bottom."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        padding: 0 1;
        height: 1fr;
    }

    #channels {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, server: SimulatedPSUServer) -> None:
        super().__init__()
        self._server = server

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="body"):
            yield _PsuPanel(self._server)
            yield Vertical(id="channels")
            yield _AddChannelPanel()
        yield _LogPanel(self._server)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Simulated PSU"
        self.sub_title = f"{self._server._host}:{self._server._port}"
        container = self.query_one("#channels", Vertical)
        with self._server.lock:
            channel_ids = [c.channel_id for c in self._server.psu.channels]
        for ch_id in channel_ids:
            container.mount(_ChannelPanel(self._server, ch_id))
        self.set_interval(0.25, self._refresh)
        # Move focus to the first ListView so the keyboard works immediately.
        self.call_after_refresh(self._focus_first_list)

    def _focus_first_list(self) -> None:
        for lv in self.query(ListView).results():
            lv.focus()
            return

    def on_key(self, event: events.Key) -> None:
        """Wrap arrow-key navigation across consecutive ListViews so the user can move from one channel's actions straight into the next channel's."""
        focused = self.focused
        if not isinstance(focused, ListView):
            return
        if event.key == "down" and focused.index is not None and focused.index >= len(focused) - 1:
            if self._focus_sibling_list(focused, +1):
                event.stop()
        elif event.key == "up" and (focused.index is None or focused.index <= 0):
            if self._focus_sibling_list(focused, -1):
                event.stop()

    def _focus_sibling_list(self, current: ListView, direction: int) -> bool:
        lists = list(self.query(ListView).results())
        try:
            idx = lists.index(current)
        except ValueError:
            return False
        target_idx = idx + direction
        if not 0 <= target_idx < len(lists):
            return False
        target = lists[target_idx]
        target.focus()
        target.index = 0 if direction > 0 else max(0, len(target) - 1)
        return True

    def _refresh(self) -> None:
        for psu_panel in self.query(_PsuPanel).results():
            psu_panel.refresh_state()
        for channel_panel in self.query(_ChannelPanel).results():
            channel_panel.refresh_state()
        for log_panel in self.query(_LogPanel).results():
            log_panel.refresh_state()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        list_id = event.list_view.id
        item_id = event.item.id
        if list_id == "add-channel" and item_id == "add":
            self._add_channel()
            return
        if list_id and list_id.endswith(_ACTIONS_LIST_SUFFIX):
            try:
                ch_id = int(list_id[len("ch-") : -len(_ACTIONS_LIST_SUFFIX)])
            except ValueError:
                return
            if item_id == "load":
                self._prompt_set(ch_id, "load", "Load resistance (ohms):")
            elif item_id == "emf":
                self._prompt_set(ch_id, "emf", "Series EMF (volts):")
            elif item_id == "probe":
                self._prompt_set(ch_id, "probe", "Probe resistance (ohms):")
            elif item_id == "remove":
                self._remove_channel(ch_id)

    # ---- channel actions ----

    def _add_channel(self) -> None:
        with self._server.lock:
            next_id = max((c.channel_id for c in self._server.psu.channels), default=0) + 1
            self._server.psu.channels.append(SimulatedPSUChannel(channel_id=next_id))
        self.query_one("#channels", Vertical).mount(_ChannelPanel(self._server, next_id))

    def _remove_channel(self, ch_id: int) -> None:
        with self._server.lock:
            self._server.psu.channels = [c for c in self._server.psu.channels if c.channel_id != ch_id]
        try:
            self.query_one(f"#ch-{ch_id}", _ChannelPanel).remove()
        except Exception:
            pass

    def _prompt_set(self, ch_id: int, param: str, prompt: str) -> None:
        with self._server.lock:
            ch = self._server.psu._channel(ch_id)
            current = ""
            if ch is not None:
                if param == "load":
                    current = str(ch.load.resistance)
                elif param == "emf":
                    current = str(ch.load.emf)
                elif param == "probe":
                    current = str(ch.load.probe_resistance)

        def _on_value(value_str: str | None) -> None:
            if not value_str:
                return
            try:
                value = float(value_str)
            except ValueError:
                return
            with self._server.lock:
                ch = self._server.psu._channel(ch_id)
                if ch is None:
                    return
                if param == "load":
                    ch.load.resistance = value
                elif param == "emf":
                    ch.load.emf = value
                elif param == "probe":
                    ch.load.probe_resistance = value
                self._server.psu._update()

        self.push_screen(_PromptScreen(prompt, initial=current), _on_value)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the simulated PSU as a TUI. The SCPI server "
            "listens in a background thread while a sidebar menu drives live edits "
            "to channel loads."
        ),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="TCP host to bind to")
    parser.add_argument(
        "--channels",
        type=int,
        default=DEFAULT_NUM_CHANNELS,
        help="Initial channel count",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

    psu = SimulatedPSU(num_channels=args.channels)
    server = SimulatedPSUServer(psu, host=args.host, port=args.port)
    server.start()
    try:
        SimulatedPSUApp(server).run()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
