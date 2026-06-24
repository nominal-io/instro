"""Tests for the cpppo simulator support."""

from __future__ import annotations

import errno

from tests.cpppo_sim_server import (
    WINDOWS_SOCKET_ACCESS_DENIED,
    build_cpppo_argv,
    is_retryable_bind_error,
)


class WindowsSocketAccessDenied(OSError):
    winerror: int


def test_retryable_bind_error_includes_address_in_use() -> None:
    assert is_retryable_bind_error(OSError(errno.EADDRINUSE, "address in use"))


def test_retryable_bind_error_includes_windows_socket_access_denied() -> None:
    error = WindowsSocketAccessDenied(errno.EACCES, "access denied")
    error.winerror = WINDOWS_SOCKET_ACCESS_DENIED

    assert is_retryable_bind_error(error)


def test_retryable_bind_error_rejects_other_os_errors() -> None:
    assert not is_retryable_bind_error(OSError(errno.ECONNREFUSED, "connection refused"))


def test_cpppo_argv_disables_udp() -> None:
    assert build_cpppo_argv(["test_dint=DINT"], "127.0.0.1", 12345) == [
        "test_dint=DINT",
        "--address",
        "127.0.0.1:12345",
        "--no-udp",
    ]
