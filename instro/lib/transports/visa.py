"""VISA transport driver. Wraps pyvisa; callers own command strings, this owns I/O and locking."""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import logging
import socket
import threading
import typing

import pyvisa
from pyvisa.constants import InterfaceType
from pyvisa.constants import Parity as VisaParity

logger = logging.getLogger(__name__)

DEFAULT_VISA_BACKEND = "@ivi"
FALLBACK_VISA_BACKEND = "@py"


class StopBits(enum.Enum):
    ONE = 1
    ONE_POINT_FIVE = 1.5
    TWO = 2


class Parity(enum.Enum):
    NONE = "N"
    ODD = "O"
    EVEN = "E"
    MARK = "M"
    SPACE = "S"


class ControlFlow(enum.IntEnum):
    NONE = 0
    XON_XOFF = 1
    RTS_CTS = 2
    DTR_DSR = 4


_PARITY_MAP = {
    Parity.NONE: VisaParity.none,
    Parity.EVEN: VisaParity.even,
    Parity.ODD: VisaParity.odd,
    Parity.MARK: VisaParity.mark,
    Parity.SPACE: VisaParity.space,
}


@dataclasses.dataclass
class SerialConfig:
    """Serial-line settings, applied when the VISA resource is an ASRL interface."""

    baud_rate: int = 9600
    data_bits: int = 8
    stop_bits: StopBits = StopBits.ONE
    parity: Parity = Parity.NONE
    flow_control: ControlFlow = ControlFlow.NONE


@dataclasses.dataclass
class TerminatorConfig:
    """Read and write terminators applied to the VISA resource."""

    read: str = "\n"
    write: str = "\r\n"


@dataclasses.dataclass
class TimeoutConfig:
    """Operation timeouts in seconds.

    `recv` is applied as the pyvisa session timeout once the resource is open;
    `connect` and `send` are reserved for future per-operation overrides.
    """

    connect: int = 30
    recv: int = 15
    send: int = 15


@dataclasses.dataclass
class VisaConfig:
    """Connection parameters for a VISA resource.

    Attributes:
        visa_resource: VISA resource string, e.g. ``TCPIP0::host::5025::SOCKET``
            or ``USB0::0x2A8D::0x0101::MY12345::INSTR``.
        visa_backend: pyvisa backend specifier. When unset (``None``), uses the
            system IVI VISA implementation (``@ivi``) and falls back to ``@py``
            when no IVI backend is installed. An explicitly set backend is used
            as-is, with no fallback.
        serial_config: Serial settings applied when the VISA resource is an
            ASRL (RS-232/RS-485) interface.
        terminator: Read and write terminators.
        timeout: Operation timeouts.
        tcp_nodelay: Disable Nagle's algorithm on raw TCP SOCKET connections.
            NI-VISA does this by default; pyvisa-py does not, which can wedge
            instruments that reset on coalesced writes (issue #156). No effect
            on non-socket transports. Defaults to ``True``.
    """

    visa_resource: str
    visa_backend: str | None = None
    serial_config: SerialConfig = dataclasses.field(default_factory=SerialConfig)
    terminator: TerminatorConfig = dataclasses.field(default_factory=TerminatorConfig)
    timeout: TimeoutConfig = dataclasses.field(default_factory=TimeoutConfig)
    tcp_nodelay: bool = True


class VisaDriver:
    """Transport for VISA-attached instruments. Composed by concrete drivers, not extended.

    Thread-safe at the I/O level via an internal lock; use :meth:`lock` to keep
    a multi-step VISA sequence atomic.
    """

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        """Construct from a VISA resource string (uses defaults) or a full ``VisaConfig``."""
        self._connection_config = _coerce_connection_config(visa_resource)
        self._inst: pyvisa.resources.MessageBasedResource | None = None
        self._lock = threading.RLock()

    def __del__(self) -> None:
        """Best-effort close on garbage collection."""
        try:
            self.close()
        except Exception:
            pass

    @property
    def is_open(self) -> bool:
        """Whether the underlying VISA resource is currently open."""
        return self._inst is not None

    def open(self) -> None:
        """Open the VISA resource and apply terminator, serial, and timeout config. Idempotent."""
        with self._lock:
            if self._inst is not None:
                return

            cfg = self._connection_config
            logger.info(
                "Opening VISA resource %s on backend %s",
                cfg.visa_resource,
                cfg.visa_backend or DEFAULT_VISA_BACKEND,
            )
            # pyvisa caches one ResourceManager per backend and shares it across every
            # driver in the process; closing it would kill all other drivers' sessions.
            # pyvisa closes it via its own atexit handler.
            rm = _open_resource_manager(cfg.visa_backend)
            inst: pyvisa.resources.MessageBasedResource | None = None
            try:
                inst = typing.cast(
                    pyvisa.resources.MessageBasedResource,
                    rm.open_resource(cfg.visa_resource),
                )
                _configure_resource(inst, cfg)
            except Exception:
                if inst is not None:
                    with contextlib.suppress(Exception):
                        inst.close()
                raise

            self._inst = inst

    def close(self) -> None:
        """Close the VISA resource. Idempotent."""
        with self._lock:
            if self._inst is None:
                return
            inst = self._inst
            try:
                inst.close()
            finally:
                self._inst = None

    def write(self, command: str) -> None:
        """Write ``command`` to the instrument; the configured write terminator is appended."""
        with self._lock:
            inst = self._require_open_locked()
            inst.write(command)

    def read(self) -> str:
        """Read a response, stripping the configured read terminator."""
        with self._lock:
            inst = self._require_open_locked()
            return inst.read()

    def query(self, command: str) -> str:
        """Write ``command`` and read the response."""
        with self._lock:
            inst = self._require_open_locked()
            return inst.query(command)

    def write_raw(self, data: bytes) -> None:
        """Write raw bytes verbatim. Caller owns framing for binary payloads."""
        with self._lock:
            inst = self._require_open_locked()
            inst.write_raw(data)

    def read_raw(self) -> bytes:
        """Read raw bytes from the instrument."""
        with self._lock:
            inst = self._require_open_locked()
            return inst.read_raw()

    def query_raw(self, command: str) -> bytes:
        """Write ``command`` (with terminator) and read raw bytes — reply is not decoded or stripped."""
        with self._lock:
            inst = self._require_open_locked()
            inst.write(command)
            return inst.read_raw()

    def query_binary_values(
        self,
        command: str,
        datatype: str = "B",
        is_big_endian: bool = False,
        container: type = list,
    ) -> typing.Any:
        """Send ``command`` and decode the IEEE-488.2 definite-length binary block reply.

        Use for waveforms, screenshots, settings dumps.

        Args:
            command: SCPI query that returns a binary block (e.g. ``"CURV?"``).
            datatype: ``struct``-style format char (``"B"`` u8, ``"h"`` i16, ``"H"`` u16, ``"f"`` f32).
            is_big_endian: Byte order of multi-byte elements.
            container: Container for decoded values (default ``list``).
        """
        with self._lock:
            inst = self._require_open_locked()
            return inst.query_binary_values(
                command,
                datatype=datatype,  # type: ignore[arg-type]
                is_big_endian=is_big_endian,
                container=container,
            )

    def clear(self) -> None:
        """VISA device clear — aborts any pending operation. Use after a timed-out blocking read."""
        with self._lock:
            inst = self._require_open_locked()
            inst.clear()

    @contextlib.contextmanager
    def temporary_timeout(self, timeout_ms: int) -> typing.Iterator[None]:
        """Hold the lock and override the operation timeout to ``timeout_ms`` ms; restored on exit (even if raises)."""
        with self._lock:
            inst = self._require_open_locked()
            original = inst.timeout
            inst.timeout = timeout_ms
            try:
                yield
            finally:
                inst.timeout = original

    def lock(self) -> threading.RLock:
        """Return the reentrant resource lock for atomic multi-step VISA sequences.

        Example::

            with driver.lock():
                driver.write("CONF:VOLT:DC")
                driver.write("RANGE 10")
                value = driver.query("READ?")

        Reentrant: the holding thread can call ``write``/``query``/``read`` inside the ``with``.
        """
        return self._lock

    def _require_open_locked(self) -> pyvisa.resources.MessageBasedResource:
        if self._inst is None:
            raise RuntimeError(
                f"VisaDriver is not open. Call open() first. Resource: {self._connection_config.visa_resource}"
            )
        return self._inst


def _open_resource_manager(backend: str | None) -> pyvisa.ResourceManager:
    """Open a ResourceManager. An unset backend uses ``@ivi`` and falls back to ``@py``; an explicit backend is used as-is."""
    if backend is not None:
        return pyvisa.ResourceManager(backend)
    try:
        return pyvisa.ResourceManager(DEFAULT_VISA_BACKEND)
    except (OSError, pyvisa.errors.Error) as exc:
        logger.warning(
            "VISA backend %s unavailable (%s); falling back to %s",
            DEFAULT_VISA_BACKEND,
            exc,
            FALLBACK_VISA_BACKEND,
        )
        return pyvisa.ResourceManager(FALLBACK_VISA_BACKEND)


def _configure_resource(
    inst: pyvisa.resources.MessageBasedResource,
    cfg: VisaConfig,
) -> None:
    inst.read_termination = cfg.terminator.read
    inst.write_termination = cfg.terminator.write
    inst.timeout = cfg.timeout.recv * 1000

    if cfg.tcp_nodelay:
        _disable_nagle(inst)

    if inst.interface_type != InterfaceType.asrl:
        return

    serial = cfg.serial_config
    inst.baud_rate = serial.baud_rate  # type: ignore[attr-defined]
    inst.data_bits = serial.data_bits  # type: ignore[attr-defined]
    inst.stop_bits = int(serial.stop_bits.value * 10)  # type: ignore[attr-defined]
    inst.parity = _PARITY_MAP[serial.parity]  # type: ignore[attr-defined]
    inst.flow_control = serial.flow_control  # type: ignore[attr-defined]


def _disable_nagle(inst: pyvisa.resources.MessageBasedResource) -> None:
    """Turn off Nagle on a raw TCP socket. NI-VISA does this by default; pyvisa-py does not (issue #156)."""
    sock = _pyvisa_py_socket(inst)
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError as exc:
        logger.warning("Could not disable Nagle (TCP_NODELAY) on %s: %s", inst.resource_name, exc)


def _pyvisa_py_socket(inst: pyvisa.resources.MessageBasedResource) -> socket.socket | None:
    """Return the raw socket backing a pyvisa-py TCPIP SOCKET session, else None (e.g. NI-VISA, serial)."""
    sessions = getattr(getattr(inst, "visalib", None), "sessions", None)
    if not isinstance(sessions, dict):
        return None
    session = sessions.get(getattr(inst, "session", None))
    sock = getattr(session, "interface", None)
    return sock if isinstance(sock, socket.socket) else None


def _coerce_connection_config(visa_resource: str | VisaConfig) -> VisaConfig:
    if isinstance(visa_resource, VisaConfig):
        return visa_resource
    if isinstance(visa_resource, str):
        return VisaConfig(visa_resource=visa_resource)
    raise TypeError("visa_resource must be a VISA resource string or VisaConfig")
