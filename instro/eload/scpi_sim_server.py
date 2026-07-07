"""In-process SCPI electronic-load emulator."""

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

from instro.eload.types import LoadMode

logger = logging.getLogger(__name__)

DEFAULT_PORT = 5026
DEFAULT_NUM_CHANNELS = 1
DEFAULT_SOURCE_VOLTAGE = 12.0  # volts
DEFAULT_SOURCE_RESISTANCE = 0.5  # ohms
DEFAULT_CURRENT_MAX = 30.0
DEFAULT_VOLTAGE_MAX = 150.0
DEFAULT_POWER_MAX = 300.0
DEFAULT_RESISTANCE_MAX = 10_000.0
SLEW_MIN = 0.001  # A/us
SLEW_MAX = 2.5  # A/us
LEVEL_MIN = 0.0


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


class OperatingState(Enum):
    OFF = "OFF"
    CC = "CC"  # current regulated
    CV = "CV"  # voltage regulated
    CP = "CP"  # power regulated
    CR = "CR"  # resistance regulated
    UNREG = "UNREG"  # source cannot satisfy the setpoint
    SHORT = "SHORT"  # input shorted


_MODE_STATE = {
    LoadMode.CC: OperatingState.CC,
    LoadMode.CV: OperatingState.CV,
    LoadMode.CP: OperatingState.CP,
    LoadMode.CR: OperatingState.CR,
}

_FUNCTION_TOKENS = {
    "CURR": LoadMode.CC,
    "CURRENT": LoadMode.CC,
    "VOLT": LoadMode.CV,
    "VOLTAGE": LoadMode.CV,
    "POW": LoadMode.CP,
    "POWER": LoadMode.CP,
    "RES": LoadMode.CR,
    "RESISTANCE": LoadMode.CR,
}

_FUNCTION_NAMES = {
    LoadMode.CC: "CURR",
    LoadMode.CV: "VOLT",
    LoadMode.CP: "POW",
    LoadMode.CR: "RES",
}


class SimulatedSource:
    """Thevenin source attached to a channel: open-circuit voltage behind a series resistance."""

    def __init__(
        self,
        voltage: float = DEFAULT_SOURCE_VOLTAGE,
        resistance: float = DEFAULT_SOURCE_RESISTANCE,
    ) -> None:
        self.voltage = voltage
        self.resistance = resistance


class SimulatedELoadChannel:
    """Per-channel state: function, setpoints, ranges, slew, and observed values."""

    def __init__(
        self,
        channel_id: int,
        source: SimulatedSource | None = None,
    ) -> None:
        self.channel_id = channel_id
        self.current_max = DEFAULT_CURRENT_MAX
        self.voltage_max = DEFAULT_VOLTAGE_MAX
        self.power_max = DEFAULT_POWER_MAX
        self.resistance_max = DEFAULT_RESISTANCE_MAX
        self.function = LoadMode.CC
        self.current_setpoint = 0.0
        self.voltage_setpoint = 0.0
        self.power_setpoint = 0.0
        self.resistance_setpoint = 0.0
        self.current_range = self.current_max
        self.voltage_range = self.voltage_max
        self.power_range = self.power_max
        self.resistance_range = self.resistance_max
        self.current_limit = self.current_max
        self.slew_rise = SLEW_MAX
        self.slew_fall = SLEW_MAX
        self.input_enabled = False
        self.shorted = False
        self.source = source if source is not None else SimulatedSource()
        # Observed / measured state
        self.terminal_voltage = 0.0
        self.current = 0.0
        self.state = OperatingState.OFF

    def setpoint(self, mode: LoadMode) -> float:
        return {
            LoadMode.CC: self.current_setpoint,
            LoadMode.CV: self.voltage_setpoint,
            LoadMode.CP: self.power_setpoint,
            LoadMode.CR: self.resistance_setpoint,
        }[mode]

    def range(self, mode: LoadMode) -> float:
        return {
            LoadMode.CC: self.current_range,
            LoadMode.CV: self.voltage_range,
            LoadMode.CP: self.power_range,
            LoadMode.CR: self.resistance_range,
        }[mode]


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


class SimulatedELoad:
    """Simulated programmable electronic load."""

    id = "NOMINAL,SIMULATED_ELOAD,000001,1.0"

    def __init__(
        self,
        num_channels: int = DEFAULT_NUM_CHANNELS,
        channels: list[SimulatedELoadChannel] | None = None,
    ) -> None:
        if channels is not None:
            self.channels: list[SimulatedELoadChannel] = channels
        else:
            self.channels = [SimulatedELoadChannel(i) for i in range(1, num_channels + 1)]
        self._error_queue: deque[int] = deque()
        # Rolling SCPI command log for the TUI. Monotonic counter lets the
        # log panel write only new entries on each refresh tick.
        self._command_log: deque[str] = deque(maxlen=200)
        self._command_log_seq = 0

    # ---- Channel lookup and error queue ----

    def _channel(self, channel_id: int) -> SimulatedELoadChannel | None:
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
                ch.current_max,
                ch.voltage_max,
                ch.power_max,
                ch.resistance_max,
            )
            source = ch.source
            ch.__init__(ch.channel_id, source)  # type: ignore[misc]
            (
                ch.current_max,
                ch.voltage_max,
                ch.power_max,
                ch.resistance_max,
            ) = limits
            ch.current_range = ch.current_max
            ch.voltage_range = ch.voltage_max
            ch.power_range = ch.power_max
            ch.resistance_range = ch.resistance_max
            ch.current_limit = ch.current_max
        self._error_queue.clear()

    def _clear_status(self, channel: int, args: list[str]) -> None:
        self._error_queue.clear()

    # ---- SOURce subsystem ----

    def _set_function(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        mode = _FUNCTION_TOKENS.get(args[0].upper())
        if mode is None:
            self._push_error(SCPIError.ILLEGAL_PARAMETER_VALUE)
            return
        ch.function = mode
        self._update()

    def _query_function(self, channel: int, args: list[str]) -> str:
        ch = self._require_channel(channel)
        if ch is None:
            return ""
        return _FUNCTION_NAMES[ch.function]

    def _set_cc_level(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        level = self._parse_ranged_value(args[0], LEVEL_MIN, ch.current_range)
        if level is None:
            return
        ch.current_setpoint = level
        self._update()

    def _query_cc_level(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.current_setpoint

    def _set_cv_level(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        level = self._parse_ranged_value(args[0], LEVEL_MIN, ch.voltage_range)
        if level is None:
            return
        ch.voltage_setpoint = level
        self._update()

    def _query_cv_level(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.voltage_setpoint

    def _set_cp_level(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        level = self._parse_ranged_value(args[0], LEVEL_MIN, ch.power_range)
        if level is None:
            return
        ch.power_setpoint = level
        self._update()

    def _query_cp_level(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.power_setpoint

    def _set_cr_level(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        level = self._parse_ranged_value(args[0], LEVEL_MIN, ch.resistance_range)
        if level is None:
            return
        ch.resistance_setpoint = level
        self._update()

    def _query_cr_level(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.resistance_setpoint

    def _set_current_range(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        value = self._parse_ranged_value(args[0], LEVEL_MIN, ch.current_max)
        if value is None:
            return
        ch.current_range = value
        ch.current_setpoint = min(ch.current_setpoint, value)
        self._update()

    def _query_current_range(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.current_range

    def _set_voltage_range(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        value = self._parse_ranged_value(args[0], LEVEL_MIN, ch.voltage_max)
        if value is None:
            return
        ch.voltage_range = value
        ch.voltage_setpoint = min(ch.voltage_setpoint, value)
        self._update()

    def _query_voltage_range(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.voltage_range

    def _set_power_range(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        value = self._parse_ranged_value(args[0], LEVEL_MIN, ch.power_max)
        if value is None:
            return
        ch.power_range = value
        ch.power_setpoint = min(ch.power_setpoint, value)
        self._update()

    def _query_power_range(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.power_range

    def _set_resistance_range(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        value = self._parse_ranged_value(args[0], LEVEL_MIN, ch.resistance_max)
        if value is None:
            return
        ch.resistance_range = value
        ch.resistance_setpoint = min(ch.resistance_setpoint, value)
        self._update()

    def _query_resistance_range(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.resistance_range

    def _set_current_limit(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        value = self._parse_ranged_value(args[0], LEVEL_MIN, ch.current_max)
        if value is None:
            return
        ch.current_limit = value
        self._update()

    def _query_current_limit(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.current_limit

    def _set_slew_both(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        rate = self._parse_ranged_value(args[0], SLEW_MIN, SLEW_MAX)
        if rate is None:
            return
        ch.slew_rise = rate
        ch.slew_fall = rate

    def _set_slew_rise(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        rate = self._parse_ranged_value(args[0], SLEW_MIN, SLEW_MAX)
        if rate is None:
            return
        ch.slew_rise = rate

    def _query_slew_rise(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.slew_rise

    def _set_slew_fall(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        rate = self._parse_ranged_value(args[0], SLEW_MIN, SLEW_MAX)
        if rate is None:
            return
        ch.slew_fall = rate

    def _query_slew_fall(self, channel: int, args: list[str]) -> float:
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return ch.slew_fall

    # ---- INPut subsystem ----

    def _set_input(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        enable = self._parse_bool(args[0])
        if enable is None:
            return
        ch.input_enabled = enable
        self._update()

    def _query_input(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.input_enabled) else 0

    def _set_short(self, channel: int, args: list[str]) -> None:
        ch = self._require_channel(channel)
        if ch is None or not self._require_args(args):
            return
        enable = self._parse_bool(args[0])
        if enable is None:
            return
        ch.shorted = enable
        self._update()

    def _query_short(self, channel: int, args: list[str]) -> int:
        ch = self._require_channel(channel)
        return 1 if (ch and ch.shorted) else 0

    # ---- MEASure subsystem ----

    def _measure_voltage(self, channel: int, args: list[str]) -> float:
        self._update()
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return add_noise(ch.terminal_voltage, 0.005)

    def _measure_current(self, channel: int, args: list[str]) -> float:
        self._update()
        ch = self._require_channel(channel)
        return add_noise(ch.current, 0.005) if ch else 0.0

    def _measure_power(self, channel: int, args: list[str]) -> float:
        self._update()
        ch = self._require_channel(channel)
        if ch is None:
            return 0.0
        return add_noise(ch.terminal_voltage * ch.current, 0.005)

    # ---- Helpers ----

    def _require_channel(self, channel: int) -> SimulatedELoadChannel | None:
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

    # ---- Physics ----

    def _update(self) -> None:
        for ch in self.channels:
            self._update_channel(ch)

    def _update_channel(self, ch: SimulatedELoadChannel) -> None:
        v_oc = ch.source.voltage
        r_s = ch.source.resistance

        if not ch.input_enabled:
            ch.current = 0.0
            ch.terminal_voltage = v_oc
            ch.state = OperatingState.OFF
            return

        if ch.shorted:
            self._apply_short(ch, v_oc, r_s)
            return

        if v_oc <= 0:
            ch.current = 0.0
            ch.terminal_voltage = v_oc
            ch.state = OperatingState.UNREG
            return

        if ch.function is LoadMode.CC:
            self._regulate_current(ch, v_oc, r_s)
        elif ch.function is LoadMode.CV:
            self._regulate_voltage(ch, v_oc, r_s)
        elif ch.function is LoadMode.CP:
            self._regulate_power(ch, v_oc, r_s)
        else:
            self._regulate_resistance(ch, v_oc, r_s)

    def _apply_short(self, ch: SimulatedELoadChannel, v_oc: float, r_s: float) -> None:
        if v_oc <= 0:
            current = 0.0
        elif r_s == 0:
            current = ch.current_max
        else:
            current = min(v_oc / r_s, ch.current_max)
        ch.current = current
        ch.terminal_voltage = max(v_oc - current * r_s, 0.0)
        ch.state = OperatingState.SHORT

    def _regulate_current(self, ch: SimulatedELoadChannel, v_oc: float, r_s: float) -> None:
        i_available = math.inf if r_s == 0 else v_oc / r_s
        if ch.current_setpoint <= i_available:
            ch.current = ch.current_setpoint
            ch.terminal_voltage = v_oc - ch.current * r_s
            ch.state = OperatingState.CC
        else:
            ch.current = i_available
            ch.terminal_voltage = 0.0
            ch.state = OperatingState.UNREG

    def _regulate_voltage(self, ch: SimulatedELoadChannel, v_oc: float, r_s: float) -> None:
        v_set = ch.voltage_setpoint
        if v_set > v_oc:
            ch.current = 0.0
            ch.terminal_voltage = v_oc
            ch.state = OperatingState.UNREG
            return
        if r_s == 0:
            # The load cannot pull a stiff source below its open-circuit voltage.
            ch.current = ch.current_limit
            ch.terminal_voltage = v_oc
            ch.state = OperatingState.CC
            return
        i_demand = (v_oc - v_set) / r_s
        if i_demand > ch.current_limit:
            ch.current = ch.current_limit
            ch.terminal_voltage = v_oc - ch.current * r_s
            ch.state = OperatingState.CC
        else:
            ch.current = i_demand
            ch.terminal_voltage = v_set
            ch.state = OperatingState.CV

    def _regulate_power(self, ch: SimulatedELoadChannel, v_oc: float, r_s: float) -> None:
        p_set = ch.power_setpoint
        if p_set == 0:
            ch.current = 0.0
            ch.terminal_voltage = v_oc
            ch.state = OperatingState.CP
            return
        if r_s == 0:
            current = p_set / v_oc
            if current > ch.current_max:
                ch.current = ch.current_max
                ch.terminal_voltage = v_oc
                ch.state = OperatingState.UNREG
                return
            ch.current = current
            ch.terminal_voltage = v_oc
            ch.state = OperatingState.CP
            return
        p_available = v_oc**2 / (4 * r_s)
        if p_set > p_available:
            ch.current = min(v_oc / (2 * r_s), ch.current_max)
            ch.terminal_voltage = v_oc - ch.current * r_s
            ch.state = OperatingState.UNREG
            return
        # High-voltage root of r_s*I^2 - v_oc*I + p_set = 0.
        current = (v_oc - math.sqrt(v_oc**2 - 4 * r_s * p_set)) / (2 * r_s)
        if current > ch.current_max:
            ch.current = ch.current_max
            ch.terminal_voltage = v_oc - ch.current * r_s
            ch.state = OperatingState.UNREG
            return
        ch.current = current
        ch.terminal_voltage = v_oc - current * r_s
        ch.state = OperatingState.CP

    def _regulate_resistance(self, ch: SimulatedELoadChannel, v_oc: float, r_s: float) -> None:
        r_total = r_s + ch.resistance_setpoint
        if r_total == 0:
            ch.current = ch.current_max
            ch.terminal_voltage = 0.0
            ch.state = OperatingState.UNREG
            return
        current = v_oc / r_total
        if current > ch.current_max:
            ch.current = ch.current_max
            ch.terminal_voltage = v_oc - ch.current * r_s
            ch.state = OperatingState.UNREG
            return
        ch.current = current
        ch.terminal_voltage = current * ch.resistance_setpoint
        ch.state = OperatingState.CR


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
    _SCPICommand("*IDN", query=SimulatedELoad._get_id),
    _SCPICommand("*RST", SimulatedELoad._reset),
    _SCPICommand("*CLS", SimulatedELoad._clear_status),
    _SCPICommand("SYSTem:ERRor", query=SimulatedELoad._get_error),
    _SCPICommand(
        "[SOURce:]FUNCtion",
        SimulatedELoad._set_function,
        query=SimulatedELoad._query_function,
    ),
    _SCPICommand(
        "[SOURce:]CURRent[:LEVel][:IMMediate]",
        SimulatedELoad._set_cc_level,
        query=SimulatedELoad._query_cc_level,
    ),
    _SCPICommand(
        "[SOURce:]VOLTage[:LEVel][:IMMediate]",
        SimulatedELoad._set_cv_level,
        query=SimulatedELoad._query_cv_level,
    ),
    _SCPICommand(
        "[SOURce:]POWer[:LEVel][:IMMediate]",
        SimulatedELoad._set_cp_level,
        query=SimulatedELoad._query_cp_level,
    ),
    _SCPICommand(
        "[SOURce:]RESistance[:LEVel][:IMMediate]",
        SimulatedELoad._set_cr_level,
        query=SimulatedELoad._query_cr_level,
    ),
    _SCPICommand(
        "[SOURce:]CURRent:RANGe",
        SimulatedELoad._set_current_range,
        query=SimulatedELoad._query_current_range,
    ),
    _SCPICommand(
        "[SOURce:]VOLTage:RANGe",
        SimulatedELoad._set_voltage_range,
        query=SimulatedELoad._query_voltage_range,
    ),
    _SCPICommand(
        "[SOURce:]POWer:RANGe",
        SimulatedELoad._set_power_range,
        query=SimulatedELoad._query_power_range,
    ),
    _SCPICommand(
        "[SOURce:]RESistance:RANGe",
        SimulatedELoad._set_resistance_range,
        query=SimulatedELoad._query_resistance_range,
    ),
    _SCPICommand(
        "[SOURce:]CURRent:LIMit",
        SimulatedELoad._set_current_limit,
        query=SimulatedELoad._query_current_limit,
    ),
    _SCPICommand("[SOURce:]CURRent:SLEW[:BOTH]", SimulatedELoad._set_slew_both),
    _SCPICommand(
        "[SOURce:]CURRent:SLEW:RISE",
        SimulatedELoad._set_slew_rise,
        query=SimulatedELoad._query_slew_rise,
    ),
    _SCPICommand(
        "[SOURce:]CURRent:SLEW:FALL",
        SimulatedELoad._set_slew_fall,
        query=SimulatedELoad._query_slew_fall,
    ),
    _SCPICommand("INPut[:STATe]", SimulatedELoad._set_input, query=SimulatedELoad._query_input),
    _SCPICommand(
        "INPut:SHORt[:STATe]",
        SimulatedELoad._set_short,
        query=SimulatedELoad._query_short,
    ),
    _SCPICommand("MEASure:CURRent", query=SimulatedELoad._measure_current),
    _SCPICommand("MEASure:VOLTage", query=SimulatedELoad._measure_voltage),
    _SCPICommand("MEASure:POWer", query=SimulatedELoad._measure_power),
)


_COMMAND_TABLE: dict[str, Callable[..., Any]] = {}
for command in _SCPI_COMMANDS:
    command.register(_COMMAND_TABLE)


# ---- Background TCP server ----


class SimulatedELoadServer:
    """TCP socket server that hands incoming SCPI lines to the simulator."""

    def __init__(self, eload: SimulatedELoad, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.eload = eload
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
        self._thread = threading.Thread(target=self._run, daemon=True, name="eload-sim-server")
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
                    response = self.eload.process_scpi_command(cmd_text)
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
        height: 6;
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

    def __init__(self, server: SimulatedELoadServer, channel_id: int) -> None:
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
                yield Static(_title("Setpoints"), classes="section-title")
                yield Static(id="setpoint-info", classes="section-info")
                with Horizontal(classes="section-actions"):
                    yield _control_cell("I max", _channel_action_id(self._channel_id, "current-limit"))
            with Vertical(classes="metric-section last-section"):
                yield Static(_title("Measure"), classes="section-title")
                yield Static(id="measure-info", classes="section-info")
                with Horizontal(classes="section-actions"):
                    yield _control_cell("V max", _channel_action_id(self._channel_id, "voltage-limit"))

    def refresh_state(self) -> None:
        with self._server.lock:
            ch = self._server.eload._channel(self._channel_id)
            if ch is None:
                self.query_one("#status-info", Static).update("(removed)")
                self.query_one("#setpoint-info", Static).update("")
                self.query_one("#measure-info", Static).update("")
                return
            self._server.eload._update()
            field_width = 8
            status_text = (
                f"{_field('State', ch.state.value, width=field_width)}\n"
                f"{_field('Func', _FUNCTION_NAMES[ch.function], width=field_width)}\n"
                f"{_field('Input', 'ON' if ch.input_enabled else 'OFF', width=field_width)}\n"
                f"{_field('Short', 'ON' if ch.shorted else 'OFF', width=field_width)}\n"
                f"{_field('I lim', f'{_fmt_limit(ch.current_limit)} A', width=field_width)}"
            )
            range_units = {LoadMode.CC: "A", LoadMode.CV: "V", LoadMode.CP: "W", LoadMode.CR: "OHM"}
            setpoint_text = (
                f"{_field('CC', f'{_fmt_limit(ch.current_setpoint)} A', width=field_width)}\n"
                f"{_field('CV', f'{_fmt_limit(ch.voltage_setpoint)} V', width=field_width)}\n"
                f"{_field('CP', f'{_fmt_limit(ch.power_setpoint)} W', width=field_width)}\n"
                f"{_field('CR', f'{_fmt_limit(ch.resistance_setpoint)} OHM', width=field_width)}\n"
                f"{_field('Range', f'{_fmt_limit(ch.range(ch.function))} {range_units[ch.function]}', width=field_width)}"
            )
            measure_text = (
                f"{_field('V', f'{_fmt_limit(ch.terminal_voltage)} V', width=field_width)}\n"
                f"{_field('I', f'{_fmt_limit(ch.current)} A', width=field_width)}\n"
                f"{_field('P', f'{_fmt_limit(ch.terminal_voltage * ch.current)} W', width=field_width)}\n"
                f"{_field('Rise', f'{_fmt_limit(ch.slew_rise)} A/us', width=field_width)}\n"
                f"{_field('Fall', f'{_fmt_limit(ch.slew_fall)} A/us', width=field_width)}"
            )
        self.query_one("#status-info", Static).update(status_text)
        self.query_one("#setpoint-info", Static).update(setpoint_text)
        self.query_one("#measure-info", Static).update(measure_text)


class _SourcePanel(Container):
    """Per-channel source panel with the Thevenin source state and controls."""

    DEFAULT_CSS = _css("""
    _SourcePanel {
        border: solid @foreground-muted@;
        padding: 0 1;
        width: 28;
        height: auto;
        background: @background@;
    }

    _SourcePanel .source-info {
        height: 7;
    }

    _SourcePanel .action-row {
        height: 1;
    }

    _SourcePanel _ActionCell {
        width: 7;
    }
    """)

    def __init__(self, server: SimulatedELoadServer, channel_id: int) -> None:
        super().__init__(id=f"ch-{channel_id}-source")
        self._server = server
        self._channel_id = channel_id
        self.border_title = "SOURCE"

    @property
    def channel_id(self) -> int:
        return self._channel_id

    def compose(self) -> ComposeResult:
        yield Static(id="source-info", classes="source-info")
        with Horizontal(classes="action-row"):
            yield _control_cell("V", _channel_action_id(self._channel_id, "source-voltage"))
            yield _control_cell("R", _channel_action_id(self._channel_id, "source-resistance"))

    def refresh_state(self) -> None:
        with self._server.lock:
            ch = self._server.eload._channel(self._channel_id)
            if ch is None:
                self.query_one("#source-info", Static).update("(removed)")
                return
            source_text = (
                f"{_field('V', f'{ch.source.voltage} V', width=5)}\n"
                f"{_field('R', f'{ch.source.resistance} OHM', width=5)}"
            )
        self.query_one("#source-info", Static).update(source_text)


class _ChannelRow(Container):
    """Layout row containing one channel box and the adjacent source box."""

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

    _ChannelRow.compact _SourcePanel {
        width: 1fr;
        min-width: 28;
    }
    """)

    def __init__(self, server: SimulatedELoadServer, channel_id: int) -> None:
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
                yield _SourcePanel(self._server, self._channel_id)


class _ELoadPanel(Static):
    """Top-level E-Load info panel: identifier + VISA resource."""

    DEFAULT_CSS = _css("""
    _ELoadPanel {
        border: solid @foreground-muted@;
        padding: 0 1;
        margin: 0;
        height: auto;
        background: @background@;
    }
    """)

    def __init__(self, server: SimulatedELoadServer) -> None:
        super().__init__()
        self._server = server
        self.border_title = f"{NOMINAL_MARK} NOMINAL E-LOAD"

    def refresh_state(self) -> None:
        with self._server.lock:
            eload_id = self._server.eload.id
        resource = f"TCPIP0::{self._server._host}::{self._server._port}::SOCKET"
        self.update(f"{_field('ID', eload_id, width=14)}\n{_field('VISA', resource, width=14)}")


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

    def __init__(self, server: SimulatedELoadServer) -> None:
        super().__init__(highlight=False, max_lines=500, auto_scroll=True)
        self._server = server
        self._last_seq = 0
        self.border_title = "SCPI LOG"

    def refresh_state(self) -> None:
        with self._server.lock:
            current_seq = self._server.eload._command_log_seq
            entries = list(self._server.eload._command_log)
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


class SimulatedELoadApp(App[None]):
    """Textual app: E-Load panel on top, channels stacked vertically with per-channel actions, '+ Add channel' at the bottom."""

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

    def __init__(self, server: SimulatedELoadServer) -> None:
        super().__init__()
        self._server = server

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="body"):
            yield _ELoadPanel(self._server)
            yield Vertical(id="channels")
            yield _AddChannelPanel()
        yield _LogPanel(self._server)
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"{NOMINAL_MARK} Nominal instro"
        self.sub_title = f"Simulated E-Load | {self._server._host}:{self._server._port}"
        container = self.query_one("#channels", Vertical)
        with self._server.lock:
            channel_ids = [c.channel_id for c in self._server.eload.channels]
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
        for eload_panel in self.query(_ELoadPanel).results():
            eload_panel.refresh_state()
        for channel_panel in self.query(_ChannelPanel).results():
            try:
                channel_panel.refresh_state()
            except NoMatches:
                continue
        for source_panel in self.query(_SourcePanel).results():
            try:
                source_panel.refresh_state()
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
        if action == "source-voltage":
            self._prompt_set(ch_id, "source-voltage", "Source voltage (volts):")
        elif action == "source-resistance":
            self._prompt_set(ch_id, "source-resistance", "Source resistance (ohms):")
        elif action == "voltage-limit":
            self._prompt_set_limit(ch_id, "voltage", "V max (volts):")
        elif action == "current-limit":
            self._prompt_set_limit(ch_id, "current", "I max (amps):")
        elif action == "remove":
            self._remove_channel(ch_id)

    # ---- channel actions ----

    def _add_channel(self) -> None:
        with self._server.lock:
            next_id = max((c.channel_id for c in self._server.eload.channels), default=0) + 1
            self._server.eload.channels.append(SimulatedELoadChannel(channel_id=next_id))
        row = _ChannelRow(self._server, next_id)
        row.set_class(self.size.width < self._COMPACT_WIDTH, "compact", update=False)
        self.query_one("#channels", Vertical).mount(row)

    def _remove_channel(self, ch_id: int) -> None:
        with self._server.lock:
            self._server.eload.channels = [c for c in self._server.eload.channels if c.channel_id != ch_id]
        try:
            self.query_one(f"#ch-{ch_id}-row", _ChannelRow).remove()
        except Exception:
            pass

    def _prompt_set(self, ch_id: int, param: str, prompt: str) -> None:
        with self._server.lock:
            ch = self._server.eload._channel(ch_id)
            current = ""
            if ch is not None:
                if param == "source-voltage":
                    current = str(ch.source.voltage)
                elif param == "source-resistance":
                    current = str(ch.source.resistance)

        def _on_value(value_str: str | None) -> None:
            if not value_str:
                return
            try:
                value = float(value_str)
            except ValueError:
                return
            with self._server.lock:
                ch = self._server.eload._channel(ch_id)
                if ch is None:
                    return
                if param == "source-voltage":
                    ch.source.voltage = value
                elif param == "source-resistance":
                    if value < 0:
                        return
                    ch.source.resistance = value
                self._server.eload._update()

        self.push_screen(_PromptScreen(prompt, initial=current), _on_value)

    def _prompt_set_limit(self, ch_id: int, param: str, prompt: str) -> None:
        with self._server.lock:
            ch = self._server.eload._channel(ch_id)
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
            if value < LEVEL_MIN:
                return
            with self._server.lock:
                ch = self._server.eload._channel(ch_id)
                if ch is None:
                    return
                if param == "voltage":
                    ch.voltage_max = value
                    self._server.eload._reset(1, [])
                elif param == "current":
                    ch.current_max = value
                    self._server.eload._reset(1, [])

        self.push_screen(_PromptScreen(prompt, initial=current), _on_value)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the simulated E-Load as a TUI. The SCPI server "
            "listens in a background thread while a sidebar menu drives live edits "
            "to channel sources."
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

    eload = SimulatedELoad(num_channels=args.channels)
    server = SimulatedELoadServer(eload, host=args.host, port=args.port)
    server.start()
    try:
        SimulatedELoadApp(server).run()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
