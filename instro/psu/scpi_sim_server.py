"""In-process SCPI power-supply emulator."""

from __future__ import annotations

import argparse
import logging
import math
import random
import re
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, Callable, cast

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, Log, Static

logger = logging.getLogger(__name__)

DEFAULT_PORT = 5025
DEFAULT_NUM_CHANNELS = 2
DEFAULT_LOAD_RESISTANCE = 1000.0  # ohms
DEFAULT_PROBE_RESISTANCE = 10.0  # ohms
DEFAULT_VOLTAGE_MAX = 60.0
DEFAULT_CURRENT_MAX = 10.0
CHANNEL_MIN = 0.0
PROTECTION_MIN = 0.0


def add_noise(value: float, percent: float) -> float:
    if not math.isfinite(value):
        return value
    std_dev = abs(value) * percent / 3
    return random.gauss(value, std_dev)


class SCPIError(IntEnum):
    """SCPI and simulator error table."""

    _message: str

    NO_ERROR = (0, "No error")
    # Command errors (-100 to -178)
    COMMAND_ERROR = (-100, "Command error")
    INVALID_CHARACTER = (-101, "Invalid character")
    SYNTAX_ERROR = (-102, "Syntax error")
    INVALID_SEPARATOR = (-103, "Invalid separator")
    DATA_TYPE_ERROR = (-104, "Data type error")
    PARAMETER_NOT_ALLOWED = (-108, "Parameter not allowed")
    MISSING_PARAMETER = (-109, "Missing parameter")
    UNDEFINED_HEADER = (-113, "Undefined header")
    HEADER_SUFFIX_OUT_OF_RANGE = (-114, "Header suffix out of range")
    INVALID_SUFFIX = (-131, "Invalid suffix")
    SUFFIX_NOT_ALLOWED = (-138, "Suffix not allowed")
    INVALID_CHARACTER_DATA = (-141, "Invalid character data")
    # Execution errors (-200 to -241)
    EXECUTION_ERROR = (-200, "Execution error")
    SETTINGS_CONFLICT = (-221, "Settings conflict")
    DATA_OUT_OF_RANGE = (-222, "Data out of range")
    ILLEGAL_PARAMETER_VALUE = (-224, "Illegal parameter value")
    HARDWARE_MISSING = (-241, "Hardware missing")
    SYSTEM_ERROR = (-310, "System error")
    PV_ABOVE_OVP = (301, "PV Above OVP")
    PC_ABOVE_OCP = (303, "PC Above OCP")
    OVP_BELOW_PV = (304, "OVP Below PV")
    OCP_BELOW_PC = (305, "OCP Below PC")
    OVERCURRENT_PROTECTION_TRIPPED = (323, "Overcurrent protection tripped")
    OVERVOLTAGE_PROTECTION_TRIPPED = (324, "Overvoltage protection tripped")
    QUEUE_OVERFLOW = (-350, "Queue overflow")
    # Query errors (-400 to -440)
    QUERY_ERROR = (-400, "Query error")

    def __new__(cls, code: int, message: str) -> Any:
        obj = int.__new__(cls, code)
        obj._value_ = code
        obj._message = message
        return obj

    @classmethod
    def from_code(cls, code: int) -> "SCPIError":
        return cast(SCPIError, cls._value2member_map_[code])

    @property
    def message(self) -> str:
        return self._message


class OperatingMode(Enum):
    OFF = "OFF"
    CV = "CV"  # voltage regulated
    CC = "CC"  # current regulated


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
    """Per-channel state: setpoints, protection, sense mode, and observed values."""

    def __init__(
        self,
        channel_id: int,
        load: SimulatedLoad | None = None,
    ) -> None:
        self.channel_id = channel_id
        self.voltage_max = DEFAULT_VOLTAGE_MAX
        self.current_max = DEFAULT_CURRENT_MAX
        self.voltage_setpoint = 0.0
        self.current_limit = 0.0
        self.overvoltage_protection_level = self.voltage_max
        self.overvoltage_protection_enabled = False
        self.overcurrent_protection_level = self.current_max
        self.overcurrent_protection_enabled = False
        self.remote_sense = False
        self.output_enabled = False
        self.load = load if load is not None else SimulatedLoad()
        # Observed / measured state
        self.terminal_voltage = 0.0
        self.load_voltage = 0.0
        self.current = 0.0
        self.mode = OperatingMode.OFF
        self.overvoltage_tripped = False
        self.overcurrent_tripped = False
        self.protection_latched = False


def _normalize_header(header: str) -> tuple[str, int]:
    channel = 1
    parts: list[str] = []
    for raw in header.removeprefix(":").split(":"):
        upper = raw.upper()
        base = upper.rstrip("0123456789")
        suffix = upper[len(base) :]
        if suffix:
            channel = int(suffix)
        parts.append(base)
    return ":".join(parts), channel


class SimulatedPSU:
    """Simulated programmable power supply."""

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
        if is_query and positional:
            self._push_error(SCPIError.PARAMETER_NOT_ALLOWED)
            return None

        logger.info("Cmd %s channel=%d args=%s", key, channel, positional)
        try:
            return handler(self, channel, positional)
        except ValueError:
            logger.warning("Invalid parameter in command: %s", cmd)
            self._push_error(SCPIError.INVALID_CHARACTER_DATA)
            return None

    def _record_log(self, cmd: str, response: Any, errors_before: int) -> None:
        parts = [time.strftime("%H:%M:%S"), cmd]
        if response is not None:
            resp_text = str(response)
            if len(resp_text) > 60:
                resp_text = resp_text[:57] + "..."
            parts.append(f"-> {resp_text}")
        for code in list(self._error_queue)[errors_before:]:
            err = SCPIError.from_code(code)
            parts.append(f"! {code:+d} {err.message}")
        self._command_log.append("  ".join(parts))
        self._command_log_seq += 1

    # ---- *IDN? and SYST:ERR? ----

    def _get_id(self, channel: int, args: list[str]) -> str:
        time.sleep(0.015)
        return self.id

    def _get_error(self, channel: int, args: list[str]) -> str:
        code = self._error_queue.popleft() if self._error_queue else SCPIError.NO_ERROR.value
        err = SCPIError.from_code(code)
        return f'{code:d},"{err.message}"'

    def _reset(self, channel: int, args: list[str]) -> None:
        for ch in self.channels:
            limits = (
                ch.voltage_max,
                ch.current_max,
            )
            load = ch.load
            remote_sense = ch.remote_sense
            ch.__init__(ch.channel_id, load)  # type: ignore[misc]
            (
                ch.voltage_max,
                ch.current_max,
            ) = limits
            ch.remote_sense = remote_sense
            ch.overvoltage_protection_level = ch.voltage_max
            ch.overcurrent_protection_level = ch.current_max
        self._error_queue.clear()

    def _clear_status(self, channel: int, args: list[str]) -> None:
        self._error_queue.clear()

    # ---- SOURce subsystem ----

    def _set_voltage(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        voltage = self._parse_ranged_value(args[0], CHANNEL_MIN, ch.voltage_max)
        if voltage is None:
            return
        if voltage > ch.overvoltage_protection_level:
            self._push_error(SCPIError.PV_ABOVE_OVP)
            return
        ch.voltage_setpoint = voltage
        self._update()

    def _query_voltage(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.voltage_setpoint

    def _set_current(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        current_limit = self._parse_ranged_value(args[0], CHANNEL_MIN, ch.current_max)
        if current_limit is None:
            return
        if current_limit > ch.overcurrent_protection_level:
            self._push_error(SCPIError.PC_ABOVE_OCP)
            return
        ch.current_limit = current_limit
        self._update()

    def _query_current(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.current_limit

    def _set_ocp_level(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        level = self._parse_ranged_value(
            args[0],
            PROTECTION_MIN,
            ch.current_max,
        )
        if level is None:
            return
        if level < ch.current_limit:
            self._push_error(SCPIError.OCP_BELOW_PC)
            return
        ch.overcurrent_protection_level = level
        self._update()

    def _query_ocp_level(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.overcurrent_protection_level

    def _set_ocp_state(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        enable = self._parse_bool(args[0])
        if enable is None:
            return
        ch.overcurrent_protection_enabled = enable
        self._update()

    def _query_ocp_state(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.overcurrent_protection_enabled) else 0

    def _set_ovp_level(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        level = self._parse_ranged_value(
            args[0],
            PROTECTION_MIN,
            ch.voltage_max,
        )
        if level is None:
            return
        if level < ch.voltage_setpoint:
            self._push_error(SCPIError.OVP_BELOW_PV)
            return
        ch.overvoltage_protection_level = level
        self._update()

    def _query_ovp_level(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.overvoltage_protection_level

    def _set_ovp_state(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        enable = self._parse_bool(args[0])
        if enable is None:
            return
        ch.overvoltage_protection_enabled = enable
        self._update()

    def _query_ovp_state(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.overvoltage_protection_enabled) else 0

    # ---- OUTPut subsystem ----

    def _set_output(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        enable = self._parse_bool(args[0])
        if enable is None:
            return
        if enable:
            ch.protection_latched = False
            ch.overvoltage_tripped = False
            ch.overcurrent_tripped = False
        ch.output_enabled = enable
        self._update()

    def _query_output(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.output_enabled) else 0

    def _clear_protection_latch(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None:
            return
        ch.protection_latched = False
        ch.overvoltage_tripped = False
        ch.overcurrent_tripped = False
        self._update()

    def _query_protection_tripped(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        if ch is None:
            return 0
        return 1 if (ch.protection_latched or ch.overvoltage_tripped or ch.overcurrent_tripped) else 0

    # ---- SYSTem subsystem ----

    def _set_remote_sense(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        enable = self._parse_remote_sense_state(args[0])
        if enable is None:
            return
        ch.remote_sense = enable
        self._update()

    def _query_remote_sense(self, channel: int, args: list[str]) -> str:
        ch = self._require_channel(channel)
        return "REM" if (ch and ch.remote_sense) else "LOC"

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

    def _parse_ranged_value(self, token: str, minimum: float, maximum: float) -> float | None:
        upper = token.upper()
        if upper in ("MAX", "MAXIMUM"):
            return maximum
        if upper in ("MIN", "MINIMUM", "DEF", "DEFAULT"):
            return minimum
        value = float(token)
        if not minimum <= value <= maximum:
            self._push_error(SCPIError.DATA_OUT_OF_RANGE)
            return None
        return value

    def _parse_remote_sense_state(self, token: str) -> bool | None:
        upper = token.upper()
        if upper == "REM":
            return True
        if upper == "LOC":
            return False
        self._push_error(SCPIError.ILLEGAL_PARAMETER_VALUE)
        return None

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
            return

        self._update_voltage_source(ch)

        if ch.overvoltage_protection_enabled and self._sense_voltage(ch) > ch.overvoltage_protection_level:
            ch.protection_latched = True
            ch.output_enabled = False
            ch.overvoltage_tripped = True
            self._push_error(SCPIError.OVERVOLTAGE_PROTECTION_TRIPPED)
            ch.terminal_voltage = 0.0
            ch.load_voltage = 0.0
            ch.current = 0.0
            ch.mode = OperatingMode.OFF
            return

        if ch.overcurrent_protection_enabled and abs(ch.current) > ch.overcurrent_protection_level:
            ch.protection_latched = True
            ch.output_enabled = False
            ch.overcurrent_tripped = True
            self._push_error(SCPIError.OVERCURRENT_PROTECTION_TRIPPED)
            ch.terminal_voltage = 0.0
            ch.load_voltage = 0.0
            ch.current = 0.0
            ch.mode = OperatingMode.OFF
            return

    def _update_voltage_source(self, ch: SimulatedPSUChannel) -> None:
        v_set = ch.voltage_setpoint
        i_limit = ch.current_limit
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

        if i_demand <= i_limit:
            ch.mode = OperatingMode.CV
            ch.current = i_demand
            if ch.remote_sense:
                ch.load_voltage = v_set
                ch.terminal_voltage = v_set + i_demand * r_probe
            else:
                ch.terminal_voltage = v_set
                ch.load_voltage = v_set - i_demand * r_probe
        else:
            ch.mode = OperatingMode.CC
            ch.current = i_limit
            if math.isfinite(r_load):
                ch.load_voltage = ch.current * r_load + emf
                ch.terminal_voltage = ch.load_voltage + ch.current * r_probe
            else:
                ch.load_voltage = 0.0
                ch.terminal_voltage = 0.0

    def _sense_voltage(self, ch: SimulatedPSUChannel) -> float:
        return ch.load_voltage if ch.remote_sense else ch.terminal_voltage


@dataclass(frozen=True)
class _SCPICommand:
    command: str
    write: Callable[..., Any] | None = None
    query: Callable[..., Any] | None = None

    def headers(self) -> tuple[str, ...]:
        prefix, required, suffix = self._split_segments()
        headers = [required + suffix[:count] for count in range(len(suffix) + 1)]
        if prefix:
            headers += [prefix + header for header in headers]
        return tuple(":".join(path) for header in headers for path in _paths_for(header))

    def register(self, table: dict[str, Callable[..., Any]]) -> None:
        for header in self.headers():
            if self.write is not None:
                table[header] = self.write
            if self.query is not None:
                table[f"{header}?"] = self.query

    def _split_segments(self) -> tuple[list[str], list[str], list[str]]:
        segments = self._segments()
        required_indexes = [index for index, (_, optional) in enumerate(segments) if not optional]
        if not required_indexes:
            raise ValueError(f"unsupported SCPI optional layout: {self.command}")
        start = required_indexes[0]
        stop = required_indexes[-1] + 1
        if any(optional for _, optional in segments[start:stop]):
            raise ValueError(f"unsupported SCPI optional layout: {self.command}")
        return (
            [part for part, _ in segments[:start]],
            [part for part, _ in segments[start:stop]],
            [part for part, _ in segments[stop:]],
        )

    def _segments(self) -> list[tuple[str, bool]]:
        segments: list[tuple[str, bool]] = []
        for optional, required in re.findall(r"\[([^\]]+)\]|([^\[\]]+)", self.command):
            text = optional or required
            segments.extend((part, bool(optional)) for part in text.strip(":").split(":") if part)
        return segments


def _keyword_forms(text: str) -> tuple[str, ...]:
    long = text.upper()
    short = "".join(char for char in text if not char.isalpha() or char.isupper()).upper()
    if short == long:
        return (short,)
    return (short, long)


def _paths_for(parts: list[str]) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = [()]
    for part in parts:
        paths = [path + (form,) for path in paths for form in _keyword_forms(part)]
    return paths


_SCPI_COMMANDS = (
    _SCPICommand("*IDN", query=SimulatedPSU._get_id),
    _SCPICommand("*RST", SimulatedPSU._reset),
    _SCPICommand("*CLS", SimulatedPSU._clear_status),
    _SCPICommand("SYSTem:ERRor", query=SimulatedPSU._get_error),
    _SCPICommand("OUTPut[:STATe]", SimulatedPSU._set_output, query=SimulatedPSU._query_output),
    _SCPICommand("OUTPut:PROTection:CLEar", SimulatedPSU._clear_protection_latch),
    _SCPICommand("OUTPut:PROTection:TRIPped", query=SimulatedPSU._query_protection_tripped),
    _SCPICommand("MEASure:VOLTage", query=SimulatedPSU._measure_voltage),
    _SCPICommand("MEASure:CURRent", query=SimulatedPSU._measure_current),
    _SCPICommand(
        "[SOURce:]VOLTage[:LEVel][:IMMediate][:AMPLitude]",
        SimulatedPSU._set_voltage,
        query=SimulatedPSU._query_voltage,
    ),
    _SCPICommand(
        "[SOURce:]CURRent[:LEVel][:IMMediate][:AMPLitude]",
        SimulatedPSU._set_current,
        query=SimulatedPSU._query_current,
    ),
    _SCPICommand(
        "[SOURce:]CURRent:PROTection[:LEVel]",
        SimulatedPSU._set_ocp_level,
        query=SimulatedPSU._query_ocp_level,
    ),
    _SCPICommand(
        "[SOURce:]CURRent:PROTection:STATe",
        SimulatedPSU._set_ocp_state,
        query=SimulatedPSU._query_ocp_state,
    ),
    _SCPICommand(
        "[SOURce:]VOLTage:PROTection[:LEVel]",
        SimulatedPSU._set_ovp_level,
        query=SimulatedPSU._query_ovp_level,
    ),
    _SCPICommand(
        "[SOURce:]VOLTage:PROTection:STATe",
        SimulatedPSU._set_ovp_state,
        query=SimulatedPSU._query_ovp_state,
    ),
    _SCPICommand(
        "SYSTem:SENSe[:STATe]",
        SimulatedPSU._set_remote_sense,
        query=SimulatedPSU._query_remote_sense,
    ),
)


_COMMAND_TABLE: dict[str, Callable[..., Any]] = {}
for command in _SCPI_COMMANDS:
    command.register(_COMMAND_TABLE)


# ---- Background TCP server ----


class SimulatedPSUServer:
    """TCP socket server that hands incoming SCPI lines to the simulator."""

    def __init__(self, psu: SimulatedPSU, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.psu = psu
        self._host = host
        self._port = port
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    @property
    def port(self) -> int:
        """The bound TCP port (resolved from the OS when started with port 0)."""
        return self._port

    def start(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self._host, self._port))
        self._port = self._socket.getsockname()[1]
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

NOMINAL_MARK = "⟢"
NOMINAL_BACKGROUND = "#121212"
NOMINAL_SURFACE = "#0C0C0C"
NOMINAL_SURFACE_MUTED = "#1A1A1A"
NOMINAL_SURFACE_HOVER = "#333333"
NOMINAL_FOREGROUND = "#FFFFFF"
NOMINAL_FOREGROUND_ACTIVE = "#0C0C0C"
NOMINAL_FOREGROUND_MUTED = "#A3A3A3"
NOMINAL_FOREGROUND_ERROR = "#B91C1C"
NOMINAL_BORDER = "#333333"
NOMINAL_BORDER_MUTED = "#242424"


_CSS_TOKENS = {
    "@background@": NOMINAL_BACKGROUND,
    "@border@": NOMINAL_BORDER,
    "@border-muted@": NOMINAL_BORDER_MUTED,
    "@foreground@": NOMINAL_FOREGROUND,
    "@foreground-active@": NOMINAL_FOREGROUND_ACTIVE,
    "@foreground-error@": NOMINAL_FOREGROUND_ERROR,
    "@foreground-muted@": NOMINAL_FOREGROUND_MUTED,
    "@surface@": NOMINAL_SURFACE,
    "@surface-hover@": NOMINAL_SURFACE_HOVER,
    "@surface-muted@": NOMINAL_SURFACE_MUTED,
}


def _css(source: str) -> str:
    for token, value in _CSS_TOKENS.items():
        source = source.replace(token, value)
    return source


def _fmt_limit(value: float) -> str:
    if not math.isfinite(value):
        return "MAX" if value > 0 else "-MAX"
    return f"{value:.3f}"


def _field(label: str, value: str, width: int = 7) -> str:
    return f"[{NOMINAL_FOREGROUND_MUTED}]{label.upper() + ':':<{width}}[/] [bold {NOMINAL_FOREGROUND}]{value}[/]"


def _title(text: str, color: str = NOMINAL_FOREGROUND_MUTED) -> str:
    text = text.upper()
    return f"[bold {color}]{text}[/]"


class _PromptScreen(ModalScreen[str | None]):
    """Modal screen that prompts for a single text value."""

    DEFAULT_CSS = _css("""
    _PromptScreen {
        align: center middle;
    }
    _PromptScreen > Vertical {
        background: @surface@;
        border: solid @foreground-muted@;
        color: @foreground@;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    _PromptScreen Label {
        margin-bottom: 1;
    }
    _PromptScreen Input {
        background: @background@;
        border: solid @border@;
        color: @foreground@;
    }
    _PromptScreen Input:focus {
        border: solid @foreground-muted@;
    }
    """)

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


def _channel_action_id(channel_id: int, action: str) -> str:
    return f"ch-{channel_id}-{action}"


class ActionSelected(Message):
    """Message emitted when a TUI action is selected."""

    def __init__(self, cell: "_ActionCell") -> None:
        super().__init__()
        self.cell = cell


class _ActionCell(Static):
    """Focusable text action."""

    can_focus = True

    DEFAULT_CSS = _css("""
    _ActionCell {
        height: 1;
        padding: 0 1;
        margin: 0 1 0 0;
        content-align: center middle;
        background: @surface-hover@;
        color: @foreground@;
        text-style: bold;
        outline: none;
    }

    _ActionCell:focus {
        background: @foreground@;
        color: @foreground-active@;
        outline: none;
        text-style: bold;
    }

    _ActionCell:hover {
        background: @surface-muted@;
        color: @foreground@;
    }
    """)

    def _select(self) -> None:
        self.post_message(ActionSelected(self))

    def on_click(self, event: events.Click) -> None:
        self.focus()
        self._select()
        event.stop()

    def on_key(self, event: events.Key) -> None:
        if event.key in {"enter", "space"}:
            self._select()
            event.stop()


def _control_cell(label: str, cell_id: str, classes: str = "") -> _ActionCell:
    return _ActionCell(label.upper(), id=cell_id, classes=classes)


class _ChannelPanel(Container):
    """Per-channel panel with live status and channel controls."""

    DEFAULT_CSS = _css("""
    _ChannelPanel {
        border: solid @foreground-muted@;
        padding: 0 1;
        margin: 0 1 0 0;
        width: 1fr;
        height: auto;
        background: @background@;
    }

    _ChannelPanel .metric-row {
        height: auto;
    }

    _ChannelPanel .status-section {
        width: 1fr;
        height: auto;
        margin: 0 1 0 0;
    }

    _ChannelPanel .metric-section {
        width: 1fr;
        height: auto;
        margin: 0 1 0 0;
    }

    _ChannelPanel .last-section {
        margin: 0;
    }

    _ChannelPanel .section-title {
        height: 1;
        text-style: bold;
    }

    _ChannelPanel .section-info {
        height: 5;
    }

    _ChannelPanel .section-actions {
        height: 1;
    }

    _ChannelPanel _ActionCell {
        width: 11;
    }

    _ChannelPanel _ActionCell.remove-action {
        color: @foreground-error@;
        width: 8;
    }
    """)

    def __init__(self, server: SimulatedPSUServer, channel_id: int) -> None:
        super().__init__(id=f"ch-{channel_id}-channel")
        self._server = server
        self._channel_id = channel_id
        self.border_title = f"CHANNEL {channel_id}"

    @property
    def channel_id(self) -> int:
        return self._channel_id

    def compose(self) -> ComposeResult:
        with Horizontal(classes="metric-row"):
            with Vertical(classes="status-section"):
                yield Static(_title("Status"), classes="section-title")
                yield Static(id="status-info", classes="section-info")
                with Horizontal(classes="section-actions"):
                    yield _control_cell(
                        "Remove",
                        _channel_action_id(self._channel_id, "remove"),
                        classes="remove-action",
                    )
            with Vertical(classes="metric-section"):
                yield Static(_title("Voltage"), classes="section-title")
                yield Static(id="voltage-info", classes="section-info")
                with Horizontal(classes="section-actions"):
                    yield _control_cell("V limit", _channel_action_id(self._channel_id, "voltage-limit"))
            with Vertical(classes="metric-section last-section"):
                yield Static(_title("Current"), classes="section-title")
                yield Static(id="current-info", classes="section-info")
                with Horizontal(classes="section-actions"):
                    yield _control_cell("I limit", _channel_action_id(self._channel_id, "current-limit"))

    def refresh_state(self) -> None:
        with self._server.lock:
            ch = self._server.psu._channel(self._channel_id)
            if ch is None:
                self.query_one("#status-info", Static).update("(removed)")
                self.query_one("#voltage-info", Static).update("")
                self.query_one("#current-info", Static).update("")
                return
            self._server.psu._update()
            tripped: list[str] = []
            if ch.protection_latched:
                tripped.append("LATCHED")
            if ch.overcurrent_tripped:
                tripped.append("OCP")
            if ch.overvoltage_tripped:
                tripped.append("OVP")
            sense_label = "EXT (4-WIRE)" if ch.remote_sense else "INT (2-WIRE)"
            ovp_state = "ON" if ch.overvoltage_protection_enabled else "OFF"
            ocp_state = "ON" if ch.overcurrent_protection_enabled else "OFF"
            trip_label = ", ".join(tripped) or "-"
            field_width = 8
            status_text = (
                f"{_field('Mode', ch.mode.value, width=field_width)}\n"
                f"{_field('Output', 'ON' if ch.output_enabled else 'OFF', width=field_width)}\n"
                f"{_field('Sense', sense_label, width=field_width)}\n"
                f"{_field('Trip', trip_label, width=field_width)}"
            )
            voltage_text = (
                f"{_field('Actual', f'{_fmt_limit(ch.terminal_voltage)} V', width=field_width)}\n"
                f"{_field('Set', f'{_fmt_limit(ch.voltage_setpoint)} V', width=field_width)}\n"
                f"{_field('V limit', f'{_fmt_limit(ch.voltage_max)} V', width=field_width)}\n"
                f"{_field('OVP', f'{ovp_state} @ {_fmt_limit(ch.overvoltage_protection_level)} V', width=field_width)}"
            )
            current_text = (
                f"{_field('Actual', f'{_fmt_limit(ch.current)} A', width=field_width)}\n"
                f"{_field('Set', f'{_fmt_limit(ch.current_limit)} A', width=field_width)}\n"
                f"{_field('I limit', f'{_fmt_limit(ch.current_max)} A', width=field_width)}\n"
                f"{_field('OCP', f'{ocp_state} @ {_fmt_limit(ch.overcurrent_protection_level)} A', width=field_width)}"
            )
        self.query_one("#status-info", Static).update(status_text)
        self.query_one("#voltage-info", Static).update(voltage_text)
        self.query_one("#current-info", Static).update(current_text)


class _LoadPanel(Container):
    """Per-channel load panel with load state and controls."""

    DEFAULT_CSS = _css("""
    _LoadPanel {
        border: solid @foreground-muted@;
        padding: 0 1;
        width: 28;
        height: auto;
        background: @background@;
    }

    _LoadPanel .load-info {
        height: 6;
    }

    _LoadPanel .action-row {
        height: 1;
    }

    _LoadPanel _ActionCell {
        width: 7;
    }
    """)

    def __init__(self, server: SimulatedPSUServer, channel_id: int) -> None:
        super().__init__(id=f"ch-{channel_id}-load")
        self._server = server
        self._channel_id = channel_id
        self.border_title = "LOAD"

    @property
    def channel_id(self) -> int:
        return self._channel_id

    def compose(self) -> ComposeResult:
        yield Static(id="load-info", classes="load-info")
        with Horizontal(classes="action-row"):
            yield _control_cell("R", _channel_action_id(self._channel_id, "load"))
            yield _control_cell("EMF", _channel_action_id(self._channel_id, "emf"))

    def refresh_state(self) -> None:
        with self._server.lock:
            ch = self._server.psu._channel(self._channel_id)
            if ch is None:
                self.query_one("#load-info", Static).update("(removed)")
                return
            load_text = (
                f"{_field('R', f'{ch.load.resistance} OHM', width=5)}\n{_field('EMF', f'{ch.load.emf} V', width=5)}"
            )
        self.query_one("#load-info", Static).update(load_text)


class _ProbePanel(Container):
    """Per-channel probe panel with probe state and controls."""

    DEFAULT_CSS = _css("""
    _ProbePanel {
        border: solid @foreground-muted@;
        padding: 0 1;
        margin: 0 1 0 0;
        width: 24;
        height: auto;
        background: @background@;
    }

    _ProbePanel .probe-info {
        height: 6;
    }

    _ProbePanel .action-row {
        height: 1;
    }

    _ProbePanel _ActionCell {
        width: 9;
    }
    """)

    def __init__(self, server: SimulatedPSUServer, channel_id: int) -> None:
        super().__init__(id=f"ch-{channel_id}-probe")
        self._server = server
        self._channel_id = channel_id
        self.border_title = "PROBE"

    @property
    def channel_id(self) -> int:
        return self._channel_id

    def compose(self) -> ComposeResult:
        yield Static(id="probe-info", classes="probe-info")
        with Horizontal(classes="action-row"):
            yield _control_cell("Probe R", _channel_action_id(self._channel_id, "probe"))

    def refresh_state(self) -> None:
        with self._server.lock:
            ch = self._server.psu._channel(self._channel_id)
            if ch is None:
                self.query_one("#probe-info", Static).update("(removed)")
                return
            probe_text = _field("R", f"{ch.load.probe_resistance} OHM", width=5)
        self.query_one("#probe-info", Static).update(probe_text)


class _ChannelRow(Container):
    """Layout row containing one channel box and adjacent probe/load boxes."""

    DEFAULT_CSS = _css("""
    _ChannelRow {
        height: auto;
        margin: 0;
        background: @background@;
    }

    _ChannelRow > Horizontal {
        height: auto;
        width: 100%;
    }

    _ChannelRow .aux-row {
        height: auto;
        width: auto;
    }

    _ChannelRow.compact > Horizontal {
        layout: vertical;
    }

    _ChannelRow.compact _ChannelPanel {
        margin: 0;
        width: 100%;
    }

    _ChannelRow.compact .aux-row {
        width: 100%;
    }

    _ChannelRow.compact _ProbePanel {
        width: 1fr;
        min-width: 24;
    }

    _ChannelRow.compact _LoadPanel {
        width: 1fr;
        min-width: 28;
    }
    """)

    def __init__(self, server: SimulatedPSUServer, channel_id: int) -> None:
        super().__init__(id=f"ch-{channel_id}-row")
        self._server = server
        self._channel_id = channel_id

    @property
    def channel_id(self) -> int:
        return self._channel_id

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield _ChannelPanel(self._server, self._channel_id)
            with Horizontal(classes="aux-row"):
                yield _ProbePanel(self._server, self._channel_id)
                yield _LoadPanel(self._server, self._channel_id)


class _PsuPanel(Static):
    """Top-level PSU info panel: identifier + error queue."""

    DEFAULT_CSS = _css("""
    _PsuPanel {
        border: solid @foreground-muted@;
        padding: 0 1;
        margin: 0;
        height: auto;
        background: @background@;
    }
    """)

    def __init__(self, server: SimulatedPSUServer) -> None:
        super().__init__()
        self._server = server
        self.border_title = f"{NOMINAL_MARK} NOMINAL PSU"

    def refresh_state(self) -> None:
        with self._server.lock:
            psu_id = self._server.psu.id
        resource = f"TCPIP0::{self._server._host}::{self._server._port}::SOCKET"
        self.update(f"{_field('ID', psu_id, width=14)}\n{_field('VISA', resource, width=14)}")


class _LogPanel(Log):
    """Scrolling log of SCPI commands, responses, and errors as they arrive."""

    DEFAULT_CSS = _css("""
    _LogPanel {
        border: solid @foreground-muted@;
        height: 12;
        background: @surface@;
        color: @foreground-muted@;
        scrollbar-background: @surface@;
        scrollbar-background-active: @surface-hover@;
        scrollbar-background-hover: @surface-hover@;
        scrollbar-color: @border@;
        scrollbar-color-active: @foreground-muted@;
        scrollbar-color-hover: @foreground-muted@;
        scrollbar-corner-color: @surface@;
    }
    """)

    def __init__(self, server: SimulatedPSUServer) -> None:
        super().__init__(highlight=False, max_lines=500, auto_scroll=True)
        self._server = server
        self._last_seq = 0
        self.border_title = "SCPI LOG"

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
    """Action panel for adding channels."""

    DEFAULT_CSS = _css("""
    _AddChannelPanel {
        border: solid @foreground-muted@;
        padding: 0 1;
        margin: 0;
        height: auto;
        background: @background@;
    }
    _AddChannelPanel _ActionCell {
        width: 16;
        height: auto;
    }
    """)

    def __init__(self) -> None:
        super().__init__()
        self.border_title = "ADD CHANNEL"

    def compose(self) -> ComposeResult:
        yield _control_cell("+ channel", "add-channel")


class SimulatedPSUApp(App[None]):
    """Textual app: PSU panel on top, channels stacked vertically with per-channel actions, '+ Add channel' at the bottom."""

    _COMPACT_WIDTH = 128
    ENABLE_COMMAND_PALETTE = False

    CSS = _css("""
    Screen {
        layout: vertical;
        background: @background@;
        color: @foreground@;
    }

    Header {
        background: @surface@;
        color: @foreground@;
    }

    Footer {
        background: @surface@;
        color: @foreground-muted@;
    }

    #body {
        padding: 0 1;
        height: 1fr;
        background: @background@;
        scrollbar-background: @background@;
        scrollbar-background-active: @surface-hover@;
        scrollbar-background-hover: @surface-hover@;
        scrollbar-color: @border@;
        scrollbar-color-active: @foreground-muted@;
        scrollbar-color-hover: @foreground-muted@;
        scrollbar-corner-color: @background@;
    }

    #channels {
        height: auto;
    }
    """)

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
        self.title = f"{NOMINAL_MARK} Nominal instro"
        self.sub_title = f"Simulated PSU | {self._server._host}:{self._server._port}"
        container = self.query_one("#channels", Vertical)
        with self._server.lock:
            channel_ids = [c.channel_id for c in self._server.psu.channels]
        for ch_id in channel_ids:
            container.mount(_ChannelRow(self._server, ch_id))
        self.set_interval(0.25, self._refresh)
        self.call_after_refresh(self._sync_responsive_layout)
        self.call_after_refresh(self._focus_first_action)

    def on_resize(self, event: events.Resize) -> None:
        self._sync_responsive_layout(event.size.width)

    def _sync_responsive_layout(self, width: int | None = None) -> None:
        if width is None:
            width = self.size.width
        compact = width < self._COMPACT_WIDTH
        for row in self.query(_ChannelRow).results():
            row.set_class(compact, "compact")

    def _focus_first_action(self) -> None:
        for action in self.query(_ActionCell).results():
            if action.id and action.id.endswith("-remove"):
                continue
            action.focus()
            return

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"left", "right", "up", "down"}:
            return
        focused = self.focused
        if not isinstance(focused, _ActionCell):
            return
        if self._focus_adjacent_action(focused, event.key):
            event.stop()

    def _focus_adjacent_action(self, current: _ActionCell, direction: str) -> bool:
        actions = [action for action in self.query(_ActionCell).results() if not action.disabled]
        if current not in actions:
            return False
        regions = {action: action.region for action in actions}
        current_region = regions[current]
        same_row = [action for action in actions if regions[action].y == current_region.y]
        if direction in {"left", "right"}:
            row = sorted(same_row, key=lambda action: regions[action].x)
            index = row.index(current)
            target_index = index + (-1 if direction == "left" else 1)
            if not 0 <= target_index < len(row):
                return False
            row[target_index].focus()
            return True

        if direction == "up":
            row_y = max((regions[action].y for action in actions if regions[action].y < current_region.y), default=None)
        else:
            row_y = min((regions[action].y for action in actions if regions[action].y > current_region.y), default=None)
        if row_y is None:
            return False
        row = [action for action in actions if regions[action].y == row_y]
        target = min(row, key=lambda action: abs(regions[action].x - current_region.x))
        target.focus()
        return True

    def _refresh(self) -> None:
        for psu_panel in self.query(_PsuPanel).results():
            psu_panel.refresh_state()
        for channel_panel in self.query(_ChannelPanel).results():
            try:
                channel_panel.refresh_state()
            except NoMatches:
                continue
        for probe_panel in self.query(_ProbePanel).results():
            try:
                probe_panel.refresh_state()
            except NoMatches:
                continue
        for load_panel in self.query(_LoadPanel).results():
            try:
                load_panel.refresh_state()
            except NoMatches:
                continue
        for log_panel in self.query(_LogPanel).results():
            log_panel.refresh_state()

    def on_action_selected(self, event: ActionSelected) -> None:
        action_id = event.cell.id
        if action_id == "add-channel":
            self._add_channel()
            return
        if action_id is None or not action_id.startswith("ch-"):
            return
        try:
            _, channel_text, action = action_id.split("-", 2)
            ch_id = int(channel_text)
        except ValueError:
            return
        if action == "load":
            self._prompt_set(ch_id, "load", "Load resistance (ohms):")
        elif action == "emf":
            self._prompt_set(ch_id, "emf", "Series EMF (volts):")
        elif action == "probe":
            self._prompt_set(ch_id, "probe", "Probe resistance (ohms):")
        elif action == "voltage-limit":
            self._prompt_set_limit(ch_id, "voltage", "V limit (volts):")
        elif action == "current-limit":
            self._prompt_set_limit(ch_id, "current", "I limit (amps):")
        elif action == "remove":
            self._remove_channel(ch_id)

    # ---- channel actions ----

    def _add_channel(self) -> None:
        with self._server.lock:
            next_id = max((c.channel_id for c in self._server.psu.channels), default=0) + 1
            self._server.psu.channels.append(SimulatedPSUChannel(channel_id=next_id))
        row = _ChannelRow(self._server, next_id)
        row.set_class(self.size.width < self._COMPACT_WIDTH, "compact", update=False)
        self.query_one("#channels", Vertical).mount(row)

    def _remove_channel(self, ch_id: int) -> None:
        with self._server.lock:
            self._server.psu.channels = [c for c in self._server.psu.channels if c.channel_id != ch_id]
        try:
            self.query_one(f"#ch-{ch_id}-row", _ChannelRow).remove()
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

    def _prompt_set_limit(self, ch_id: int, param: str, prompt: str) -> None:
        with self._server.lock:
            ch = self._server.psu._channel(ch_id)
            current = ""
            if ch is not None:
                if param == "voltage":
                    current = str(ch.voltage_max)
                elif param == "current":
                    current = str(ch.current_max)

        def _on_value(value_str: str | None) -> None:
            if not value_str:
                return
            try:
                value = float(value_str)
            except ValueError:
                return
            if value < CHANNEL_MIN:
                return
            with self._server.lock:
                ch = self._server.psu._channel(ch_id)
                if ch is None:
                    return
                if param == "voltage":
                    ch.voltage_max = value
                    self._server.psu._reset(1, [])
                elif param == "current":
                    ch.current_max = value
                    self._server.psu._reset(1, [])

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
