"""In-process SCPI digital-multimeter emulator (headless)."""

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
from enum import IntEnum
from typing import Any, Callable, cast

logger = logging.getLogger(__name__)

DEFAULT_PORT = 5026

FUNCTION_DC_VOLTAGE = "VOLT:DC"
FUNCTION_AC_VOLTAGE = "VOLT:AC"
FUNCTION_DC_CURRENT = "CURR:DC"
FUNCTION_AC_CURRENT = "CURR:AC"
FUNCTION_RESISTANCE = "RES"
FUNCTIONS = (
    FUNCTION_DC_VOLTAGE,
    FUNCTION_AC_VOLTAGE,
    FUNCTION_DC_CURRENT,
    FUNCTION_AC_CURRENT,
    FUNCTION_RESISTANCE,
)
DEFAULT_FUNCTION = FUNCTION_DC_VOLTAGE
DEFAULT_STIMULUS = {
    FUNCTION_DC_VOLTAGE: 1.25,
    FUNCTION_AC_VOLTAGE: 0.5,
    FUNCTION_DC_CURRENT: 0.1,
    FUNCTION_AC_CURRENT: 0.05,
    FUNCTION_RESISTANCE: 1000.0,
}
NOISE_PERCENT = 0.005


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
    INVALID_CHARACTER_DATA = (-141, "Invalid character data")
    # Execution errors (-200 to -241)
    EXECUTION_ERROR = (-200, "Execution error")
    DATA_OUT_OF_RANGE = (-222, "Data out of range")
    ILLEGAL_PARAMETER_VALUE = (-224, "Illegal parameter value")
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


class SimulatedDMM:
    """Simulated digital multimeter: one active function, per-function stimulus returned with noise."""

    id = "NOMINAL,SIMULATED_DMM,000001,1.0"

    def __init__(self, stimulus: dict[str, float] | None = None) -> None:
        self.stimulus: dict[str, float] = dict(DEFAULT_STIMULUS)
        if stimulus is not None:
            self.stimulus.update(stimulus)
        self.function = DEFAULT_FUNCTION
        self._error_queue: deque[int] = deque()

    def set_stimulus(self, function: str, value: float) -> None:
        """Set the value the given function measures (before noise)."""
        if function not in FUNCTIONS:
            raise ValueError(f"unknown function {function!r}; expected one of {FUNCTIONS}")
        self.stimulus[function] = value

    def _push_error(self, err: SCPIError) -> None:
        self._error_queue.append(err.value)

    # ---- Top-level dispatch ----

    def process_scpi_command(self, cmd: str) -> Any:
        stripped = cmd.strip()
        if not stripped:
            return None
        return self._dispatch(stripped)

    def _dispatch(self, cmd: str) -> Any:
        header_raw, _, rest = cmd.partition(" ")
        rest = rest.strip()

        is_query = header_raw.endswith("?")
        if is_query:
            header_raw = header_raw[:-1]

        canonical, _ = _normalize_header(header_raw)

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

        logger.info("Cmd %s args=%s", key, positional)
        try:
            return handler(self, positional)
        except ValueError:
            logger.warning("Invalid parameter in command: %s", cmd)
            self._push_error(SCPIError.INVALID_CHARACTER_DATA)
            return None

    # ---- *IDN? / *RST / *CLS / SYST:ERR? ----

    def _get_id(self, args: list[str]) -> str:
        time.sleep(0.015)
        return self.id

    def _get_error(self, args: list[str]) -> str:
        code = self._error_queue.popleft() if self._error_queue else SCPIError.NO_ERROR.value
        err = SCPIError.from_code(code)
        return f'{code:d},"{err.message}"'

    def _reset(self, args: list[str]) -> None:
        # Stimulus is the simulated physical world; *RST resets instrument state only.
        self.function = DEFAULT_FUNCTION
        self._error_queue.clear()

    def _clear_status(self, args: list[str]) -> None:
        self._error_queue.clear()

    # ---- Function switching ----

    def _set_function(self, args: list[str]) -> None:
        if not self._require_args(args):
            return
        function = args[0].strip("\"'").upper()
        if function == "VOLT":
            function = FUNCTION_DC_VOLTAGE
        elif function == "CURR":
            function = FUNCTION_DC_CURRENT
        if function not in FUNCTIONS:
            self._push_error(SCPIError.ILLEGAL_PARAMETER_VALUE)
            return
        self.function = function

    def _query_function(self, args: list[str]) -> str:
        return f'"{self.function}"'

    def _read(self, args: list[str]) -> float:
        return add_noise(self.stimulus[self.function], NOISE_PERCENT)

    # ---- Helpers ----

    def _require_args(self, args: list[str]) -> bool:
        if not args:
            self._push_error(SCPIError.MISSING_PARAMETER)
            return False
        return True


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
    _SCPICommand("*IDN", query=SimulatedDMM._get_id),
    _SCPICommand("*RST", SimulatedDMM._reset),
    _SCPICommand("*CLS", SimulatedDMM._clear_status),
    _SCPICommand("SYSTem:ERRor", query=SimulatedDMM._get_error),
    _SCPICommand("[SENSe:]FUNCtion", SimulatedDMM._set_function, query=SimulatedDMM._query_function),
    _SCPICommand("READ", query=SimulatedDMM._read),
)


_COMMAND_TABLE: dict[str, Callable[..., Any]] = {}
for command in _SCPI_COMMANDS:
    command.register(_COMMAND_TABLE)


def _make_configure(function: str) -> Callable[[SimulatedDMM, list[str]], None]:
    def handler(dmm: SimulatedDMM, args: list[str]) -> None:
        dmm.function = function

    return handler


def _make_measure(function: str) -> Callable[[SimulatedDMM, list[str]], float]:
    def handler(dmm: SimulatedDMM, args: list[str]) -> float:
        dmm.function = function
        return add_noise(dmm.stimulus[function], NOISE_PERCENT)

    return handler


_FUNCTION_HEADERS = {
    FUNCTION_DC_VOLTAGE: "VOLTage[:DC]",
    FUNCTION_AC_VOLTAGE: "VOLTage:AC",
    FUNCTION_DC_CURRENT: "CURRent[:DC]",
    FUNCTION_AC_CURRENT: "CURRent:AC",
    FUNCTION_RESISTANCE: "RESistance",
}
for function, header in _FUNCTION_HEADERS.items():
    _SCPICommand(f"CONFigure:{header}", _make_configure(function)).register(_COMMAND_TABLE)
    _SCPICommand(f"MEASure:{header}", query=_make_measure(function)).register(_COMMAND_TABLE)


# ---- Background TCP server ----


class SimulatedDMMServer:
    """TCP socket server that hands incoming SCPI lines to the simulator."""

    def __init__(self, dmm: SimulatedDMM, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.dmm = dmm
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
        self._thread = threading.Thread(target=self._run, daemon=True, name="dmm-sim-server")
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
                    response = self.dmm.process_scpi_command(cmd_text)
                if response is not None:
                    try:
                        conn.sendall((str(response) + "\n").encode())
                    except OSError:
                        return


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the simulated DMM as a headless SCPI TCP server.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="TCP host to bind to")
    parser.add_argument("--dc-voltage", type=float, help="DC voltage stimulus (V)")
    parser.add_argument("--ac-voltage", type=float, help="AC voltage stimulus (V)")
    parser.add_argument("--dc-current", type=float, help="DC current stimulus (A)")
    parser.add_argument("--ac-current", type=float, help="AC current stimulus (A)")
    parser.add_argument("--resistance", type=float, help="Resistance stimulus (ohms)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    stimulus = {
        function: value
        for function, value in (
            (FUNCTION_DC_VOLTAGE, args.dc_voltage),
            (FUNCTION_AC_VOLTAGE, args.ac_voltage),
            (FUNCTION_DC_CURRENT, args.dc_current),
            (FUNCTION_AC_CURRENT, args.ac_current),
            (FUNCTION_RESISTANCE, args.resistance),
        )
        if value is not None
    }
    dmm = SimulatedDMM(stimulus=stimulus)
    server = SimulatedDMMServer(dmm, host=args.host, port=args.port)
    server.start()
    print(f"Simulated DMM listening on TCPIP0::{args.host}::{server.port}::SOCKET (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
