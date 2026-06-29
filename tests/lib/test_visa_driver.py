"""Unit tests for the standalone VisaDriver against mocked pyvisa resources."""

from __future__ import annotations

import importlib.util
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
import pyvisa
from pyvisa.constants import VI_ERROR_LIBRARY_NFOUND, InterfaceType
from pyvisa.constants import Parity as VisaParity

from instro.lib.transports import (
    ControlFlow,
    Parity,
    SerialConfig,
    StopBits,
    TerminatorConfig,
    TimeoutConfig,
    VisaConfig,
    VisaDriver,
)


def _make_config(
    *,
    visa_resource: str = "TCPIP0::127.0.0.1::5025::SOCKET",
    visa_backend: str | None = None,
    serial_config: SerialConfig | None = None,
    terminator: TerminatorConfig | None = None,
    timeout: TimeoutConfig | None = None,
    tcp_nodelay: bool = True,
) -> VisaConfig:
    kwargs = {} if visa_backend is None else {"visa_backend": visa_backend}
    return VisaConfig(
        visa_resource=visa_resource,
        serial_config=serial_config or SerialConfig(),
        terminator=terminator or TerminatorConfig(read="\n", write="\r\n"),
        timeout=timeout or TimeoutConfig(connect=30, recv=15, send=15),
        tcp_nodelay=tcp_nodelay,
        **kwargs,
    )


def _make_driver(
    visa_resource: str | VisaConfig | None = None,
) -> VisaDriver:
    if visa_resource is None:
        visa_resource = _make_config()
    return VisaDriver(visa_resource)


@pytest.fixture
def mock_pyvisa():
    """Patch pyvisa.ResourceManager and yield (rm_class, rm_instance, resource)."""
    with patch("instro.lib.transports.visa.pyvisa.ResourceManager") as rm_class:
        rm_instance = MagicMock()
        rm_class.return_value = rm_instance
        resource = MagicMock()
        resource.interface_type = InterfaceType.tcpip
        resource.query.return_value = '0,"No error"'
        rm_instance.open_resource.return_value = resource
        yield rm_class, rm_instance, resource


def test_construction_accepts_raw_resource_string(mock_pyvisa):
    rm_class, rm_instance, _ = mock_pyvisa
    driver = _make_driver("USB0::0x1234::0x5678::SERIAL::INSTR")

    driver.open()

    rm_class.assert_called_once_with("@ivi")
    rm_instance.open_resource.assert_called_once_with("USB0::0x1234::0x5678::SERIAL::INSTR")


def test_write_does_not_query_resource(mock_pyvisa):
    _, _, resource = mock_pyvisa
    driver = VisaDriver(_make_config())
    driver.open()

    driver.write("OUTP ON")

    resource.write.assert_called_once_with("OUTP ON")
    resource.query.assert_not_called()


def test_construction_rejects_invalid_connection():
    with pytest.raises(TypeError, match="visa_resource must be"):
        VisaDriver(object())  # type: ignore[arg-type]


def test_construction_does_not_open(mock_pyvisa):
    rm_class, _, _ = mock_pyvisa
    driver = _make_driver()
    assert driver.is_open is False
    rm_class.assert_not_called()


def test_open_creates_resource_and_applies_config(mock_pyvisa):
    rm_class, rm_instance, resource = mock_pyvisa
    cfg = _make_config(
        terminator=TerminatorConfig(read="\r", write="\n"),
        timeout=TimeoutConfig(connect=10, recv=20, send=5),
    )
    driver = _make_driver(cfg)

    driver.open()

    assert driver.is_open is True
    rm_class.assert_called_once_with("@ivi")
    rm_instance.open_resource.assert_called_once_with(cfg.visa_resource)
    assert resource.read_termination == "\r"
    assert resource.write_termination == "\n"
    assert resource.timeout == 20 * 1000


def test_open_is_idempotent(mock_pyvisa):
    rm_class, rm_instance, _ = mock_pyvisa
    driver = _make_driver()

    driver.open()
    driver.open()

    rm_class.assert_called_once()
    rm_instance.open_resource.assert_called_once()


def test_open_uses_ivi_backend_by_default(mock_pyvisa):
    rm_class, _, _ = mock_pyvisa
    driver = _make_driver()

    driver.open()

    rm_class.assert_called_once_with("@ivi")


def test_open_falls_back_to_py_when_ivi_backend_missing(mock_pyvisa):
    rm_class, rm_instance, _ = mock_pyvisa
    rm_class.side_effect = [OSError("Could not locate a VISA implementation"), rm_instance]
    driver = _make_driver()

    driver.open()

    assert rm_class.call_args_list == [call("@ivi"), call("@py")]
    assert driver.is_open is True


def test_open_falls_back_to_py_when_ivi_library_not_found(mock_pyvisa):
    """A missing native VISA library surfaces as VisaIOError, not OSError (issue #133)."""
    rm_class, rm_instance, _ = mock_pyvisa
    rm_class.side_effect = [pyvisa.errors.VisaIOError(VI_ERROR_LIBRARY_NFOUND), rm_instance]
    driver = _make_driver()

    driver.open()

    assert rm_class.call_args_list == [call("@ivi"), call("@py")]
    assert driver.is_open is True


@pytest.mark.parametrize("backend", ["@ivi", "@py", "@sim"])
def test_open_does_not_fall_back_for_explicit_backend(mock_pyvisa, backend: str):
    rm_class, _, _ = mock_pyvisa
    rm_class.side_effect = OSError("backend not available")
    driver = _make_driver(_make_config(visa_backend=backend))

    with pytest.raises(OSError, match="backend not available"):
        driver.open()

    rm_class.assert_called_once_with(backend)
    assert driver.is_open is False


def test_open_leaves_shared_resource_manager_open_when_open_resource_fails(mock_pyvisa):
    _, rm_instance, _ = mock_pyvisa
    rm_instance.open_resource.side_effect = RuntimeError("open failed")
    driver = _make_driver()

    with pytest.raises(RuntimeError, match="open failed"):
        driver.open()

    rm_instance.close.assert_not_called()
    assert driver.is_open is False


def test_open_closes_resource_but_not_shared_resource_manager_when_setup_fails(mock_pyvisa):
    _, rm_instance, _ = mock_pyvisa

    class FailingResource:
        interface_type = InterfaceType.tcpip

        def __init__(self) -> None:
            self.close = MagicMock()

        @property
        def read_termination(self) -> str:
            return "\n"

        @read_termination.setter
        def read_termination(self, value: str) -> None:
            raise RuntimeError("setup failed")

    resource = FailingResource()
    rm_instance.open_resource.return_value = resource
    driver = _make_driver()

    with pytest.raises(RuntimeError, match="setup failed"):
        driver.open()

    resource.close.assert_called_once()
    rm_instance.close.assert_not_called()
    assert driver.is_open is False


def test_close_before_open_is_noop():
    driver = _make_driver()
    driver.close()
    assert driver.is_open is False


def test_close_after_open_releases_resource(mock_pyvisa):
    _, rm_instance, resource = mock_pyvisa
    driver = _make_driver()

    driver.open()
    driver.close()

    resource.close.assert_called_once()
    rm_instance.close.assert_not_called()
    assert driver.is_open is False


def test_close_is_idempotent(mock_pyvisa):
    _, rm_instance, resource = mock_pyvisa
    driver = _make_driver()

    driver.open()
    driver.close()
    driver.close()

    resource.close.assert_called_once()
    rm_instance.close.assert_not_called()


def test_close_does_not_break_other_driver_on_shared_resource_manager(mock_pyvisa):
    # pyvisa returns one cached ResourceManager per backend, so two drivers share it.
    _, rm_instance, _ = mock_pyvisa
    resource_a, resource_b = MagicMock(), MagicMock()
    resource_a.interface_type = resource_b.interface_type = InterfaceType.tcpip
    resource_b.read.return_value = "ok"
    rm_instance.open_resource.side_effect = [resource_a, resource_b]

    driver_a = _make_driver(_make_config(visa_resource="TCPIP0::10.0.0.1::5025::SOCKET"))
    driver_b = _make_driver(_make_config(visa_resource="TCPIP0::10.0.0.2::5025::SOCKET"))
    driver_a.open()
    driver_b.open()

    driver_a.close()

    rm_instance.close.assert_not_called()
    assert driver_b.is_open is True
    assert driver_b.read() == "ok"


def test_open_applies_serial_config_for_asrl(mock_pyvisa):
    _, _, resource = mock_pyvisa
    resource.interface_type = InterfaceType.asrl
    serial = SerialConfig(
        baud_rate=115200,
        data_bits=8,
        stop_bits=StopBits.ONE,
        parity=Parity.SPACE,
        flow_control=ControlFlow.NONE,
    )
    driver = _make_driver(_make_config(serial_config=serial))

    driver.open()

    assert resource.baud_rate == 115200
    assert resource.data_bits == 8
    assert resource.stop_bits == 10
    assert resource.parity == VisaParity.space
    assert resource.flow_control == ControlFlow.NONE


def _attach_pyvisa_py_socket(resource: MagicMock, sock: object) -> None:
    """Make a mocked resource look like a pyvisa-py session whose .interface is ``sock``."""
    resource.session = "sess"
    resource.visalib.sessions = {"sess": SimpleNamespace(interface=sock)}


def test_open_disables_nagle_on_pyvisa_py_socket(mock_pyvisa):
    _, _, resource = mock_pyvisa
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _attach_pyvisa_py_socket(resource, sock)
    try:
        _make_driver().open()
        # macOS reads TCP_NODELAY back as a non-1 truthy value; assert "enabled", not literally 1.
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0
    finally:
        sock.close()


def test_open_leaves_nagle_untouched_when_tcp_nodelay_false(mock_pyvisa):
    _, _, resource = mock_pyvisa
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _attach_pyvisa_py_socket(resource, sock)
    try:
        _make_driver(_make_config(tcp_nodelay=False)).open()
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 0
    finally:
        sock.close()


def test_open_ignores_non_socket_backend_interface(mock_pyvisa):
    # NI-VISA has no pyvisa-py sessions dict; a non-socket .interface must be skipped without error.
    _, _, resource = mock_pyvisa
    _attach_pyvisa_py_socket(resource, object())

    driver = _make_driver()
    driver.open()

    assert driver.is_open is True


def test_write_when_not_open_raises():
    driver = _make_driver()
    with pytest.raises(RuntimeError, match="not open"):
        driver.write("*IDN?")


def test_query_when_not_open_raises():
    driver = _make_driver()
    with pytest.raises(RuntimeError, match="not open"):
        driver.query("*IDN?")


def test_read_when_not_open_raises():
    driver = _make_driver()
    with pytest.raises(RuntimeError, match="not open"):
        driver.read()


def test_raw_methods_when_not_open_raise():
    driver = _make_driver()

    with pytest.raises(RuntimeError, match="not open"):
        driver.write_raw(b"*IDN?\n")
    with pytest.raises(RuntimeError, match="not open"):
        driver.read_raw()
    with pytest.raises(RuntimeError, match="not open"):
        driver.query_raw("CURVe?")


def test_write_writes_to_resource(mock_pyvisa):
    _, _, resource = mock_pyvisa
    driver = _make_driver()
    driver.open()

    driver.write("OUTP ON")

    resource.write.assert_called_once_with("OUTP ON")
    resource.query.assert_not_called()


def test_query_returns_resource_response(mock_pyvisa):
    _, _, resource = mock_pyvisa
    resource.query.return_value = "5.0"
    driver = _make_driver()
    driver.open()

    assert driver.query("MEAS:VOLT?") == "5.0"
    resource.query.assert_called_once_with("MEAS:VOLT?")


def test_read_returns_resource_response(mock_pyvisa):
    _, _, resource = mock_pyvisa
    resource.read.return_value = "ready"
    driver = _make_driver()
    driver.open()

    assert driver.read() == "ready"
    resource.read.assert_called_once()
    resource.query.assert_not_called()


def test_write_raw_writes_bytes(mock_pyvisa):
    _, _, resource = mock_pyvisa
    driver = _make_driver()
    driver.open()

    driver.write_raw(b":SYSTem:SETup #14data")

    resource.write_raw.assert_called_once_with(b":SYSTem:SETup #14data")
    resource.query.assert_not_called()


def test_read_raw_returns_bytes(mock_pyvisa):
    _, _, resource = mock_pyvisa
    resource.read_raw.return_value = b"#14data"
    driver = _make_driver()
    driver.open()

    assert driver.read_raw() == b"#14data"
    resource.read_raw.assert_called_once()
    resource.query.assert_not_called()


def test_query_raw_writes_command_and_returns_raw_bytes(mock_pyvisa):
    _, _, resource = mock_pyvisa
    resource.read_raw.return_value = b"#14data"
    driver = _make_driver()
    driver.open()

    assert driver.query_raw("CURVe?") == b"#14data"
    resource.write.assert_called_once_with("CURVe?")
    resource.read_raw.assert_called_once()
    resource.query.assert_not_called()


def test_lock_allows_same_thread_reentry(mock_pyvisa):
    _, _, resource = mock_pyvisa
    resource.query.return_value = "5.0"
    driver = _make_driver()
    driver.open()

    with driver.lock():
        driver.write("CONF:VOLT:DC")
        driver.write("RANGE 10")
        value = driver.query("READ?")

    assert value == "5.0"
    assert resource.write.call_args_list == [
        call("CONF:VOLT:DC"),
        call("RANGE 10"),
    ]
    resource.query.assert_called_once_with("READ?")


def test_per_call_lock_serializes_concurrent_write_and_query(mock_pyvisa):
    _, _, resource = mock_pyvisa
    active_calls = 0
    overlapped = False
    state_lock = threading.Lock()

    def enter_call(response: str | None = None) -> str | None:
        nonlocal active_calls, overlapped
        with state_lock:
            active_calls += 1
            overlapped = overlapped or active_calls > 1
        time.sleep(0.01)
        with state_lock:
            active_calls -= 1
        return response

    resource.write.side_effect = lambda command: enter_call()
    resource.query.side_effect = lambda command: enter_call("ok")
    driver = _make_driver()
    driver.open()

    threads = [threading.Thread(target=driver.write, args=(f"CMD {index}",)) for index in range(5)] + [
        threading.Thread(target=driver.query, args=(f"QUERY {index}?",)) for index in range(5)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert overlapped is False
    assert resource.write.call_count == 5
    assert resource.query.call_count == 5


def test_transactional_lock_blocks_other_threads_until_released(mock_pyvisa):
    _, _, resource = mock_pyvisa
    driver = _make_driver()
    driver.open()
    inside_transaction = threading.Event()
    transaction_can_exit = threading.Event()
    write_finished = threading.Event()

    def background_transaction():
        with driver.lock():
            inside_transaction.set()
            transaction_can_exit.wait(timeout=1.0)

    def background_write():
        driver.write("BLOCKED")
        write_finished.set()

    transaction_thread = threading.Thread(target=background_transaction)
    transaction_thread.start()
    assert inside_transaction.wait(timeout=1.0)

    write_thread = threading.Thread(target=background_write)
    write_thread.start()
    assert not write_finished.wait(timeout=0.1)

    transaction_can_exit.set()
    transaction_thread.join(timeout=1.0)
    write_thread.join(timeout=1.0)

    assert write_finished.is_set()
    resource.write.assert_called_once_with("BLOCKED")


@pytest.mark.parametrize(
    "module",
    [
        "usb",  # pyusb: USB-TMC backend
        "libusb_package",  # bundled libusb so USB needs no system library
        "gpib_ctypes",  # GPIB binding
        "serial",  # pyserial: ASRL/serial backend
        "psutil",  # TCPIP resource discovery
        "zeroconf",  # HiSLIP discovery
        "pyvicp",  # VICP backend
    ],
)
def test_visa_backend_package_is_installed(module: str) -> None:
    """Every pyvisa-py backend the @py fallback relies on must ship with instro (issue #102)."""
    assert importlib.util.find_spec(module) is not None
