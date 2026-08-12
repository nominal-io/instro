"""One transport shared by a device's category views: exactly one session per box, torn down by the last view.

Given a combined instrument (one box that both sources and sinks, e.g. the EA PSB 10000), when the
user constructs the device and takes a view for each category, then the two views share that one
session for as long as either of them owns it.

Ownership is two-level: the device holds the transport on its own behalf, and tracks its views
itself. That puts device-level teardown (the remote lock, here) in the device's own release path
while the session is still up, so no teardown callback has to be threaded through the transport.
Two view classes rather than one class inheriting both contracts, because each applies its own
current-sign convention and publishes into its own category namespace. The stubs below carry real
SCPI only where this test reaches them, because it pins the ownership contract, not any vendor's
command set.
"""

from __future__ import annotations

import threading
from functools import cached_property
from unittest.mock import MagicMock, call, patch

import pytest
from pyvisa.constants import InterfaceType

from instro.eload import ELoadDriverBase, InstroELoad, LoadMode, SlewRateDirection
from instro.lib.transports import VisaDriver
from instro.psu import InstroPSU, PSUDriverBase

RESOURCE = "TCPIP0::10.0.0.5::5025::SOCKET"


class _Box:
    """Mirrors a bidirectional supply: owns the session and takes the device's remote lock once."""

    def __init__(self, visa_resource: str) -> None:
        self._visa = VisaDriver(visa_resource)
        self._ownership = threading.RLock()
        self._views: list[object] = []

    @cached_property
    def source(self) -> PSUDriverBase:
        return _SourceView(self)

    @cached_property
    def sink(self) -> ELoadDriverBase:
        return _SinkView(self)

    def acquire(self, view: object) -> None:
        with self._ownership:
            if any(held is view for held in self._views):
                return
            first = not self._views
            self._views.append(view)
            if not first:
                return
            self._visa.open(self)  # the device holds the transport, not the views
            try:
                self._visa.write("SYST:LOCK ON")
            except Exception:
                # A stranded view would make the retry report not-first, so SYST:LOCK ON
                # would never be re-attempted.
                self._views.remove(view)
                self._visa.close(self)
                raise

    def release(self, view: object) -> None:
        with self._ownership:
            self._views = [held for held in self._views if held is not view]
            if self._views:
                return
            self._visa.write("SYST:LOCK OFF")  # session still up
            self._visa.close(self)

    def measure_voltage(self) -> float:
        return float(self._visa.query("MEAS:VOLT?"))


class _SourceView(PSUDriverBase):
    """The source surface of the box."""

    def __init__(self, device: _Box) -> None:
        self._dev = device

    def open(self) -> None:
        self._dev.acquire(self)

    def close(self) -> None:
        self._dev.release(self)

    def set_voltage(self, voltage: float, channel: int) -> None:
        raise NotImplementedError

    def get_voltage(self, channel: int) -> float:
        raise NotImplementedError

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        raise NotImplementedError

    def get_current(self, channel: int) -> float:
        raise NotImplementedError

    def output_enable(self, enable: bool, channel: int) -> None:
        raise NotImplementedError

    def get_output_status(self, channel: int) -> bool:
        raise NotImplementedError


class _SinkView(ELoadDriverBase):
    """The sink surface of the same box, deliberately mirroring ``_SourceView``'s lifecycle."""

    def __init__(self, device: _Box) -> None:
        self._dev = device

    def open(self) -> None:
        self._dev.acquire(self)

    def close(self) -> None:
        self._dev.release(self)

    def short_output(self, enable: bool, channel: int) -> None:
        raise NotImplementedError

    def set_mode(self, mode: LoadMode, channel: int) -> None:
        raise NotImplementedError

    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        raise NotImplementedError

    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        raise NotImplementedError

    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        raise NotImplementedError

    def output_enable(self, enable: bool, channel: int) -> None:
        raise NotImplementedError

    def get_current(self, channel: int) -> float:
        raise NotImplementedError

    def get_voltage(self, channel: int) -> float:
        return self._dev.measure_voltage()


@pytest.fixture
def mock_pyvisa():
    """``open_resource`` returns the *same* resource mock on every call, so its call counts are per-box totals."""
    with patch("instro.lib.transports.visa.pyvisa.ResourceManager") as rm_class:
        rm_instance = MagicMock()
        rm_class.return_value = rm_instance
        resource = MagicMock()
        resource.interface_type = InterfaceType.tcpip
        # A single answer to every query, so no stub may depend on query-response ordering.
        resource.query.return_value = "48.0"
        rm_instance.open_resource.return_value = resource
        yield rm_instance, resource


def test_a_devices_views_share_one_session(mock_pyvisa):
    rm_instance, resource = mock_pyvisa

    box = _Box(RESOURCE)
    psu = InstroPSU(name="psb_source", driver=box.source, num_channels=1)
    eload = InstroELoad(name="psb_sink", driver=box.sink)

    psu.open()
    eload.open()

    # One socket for the box, and the device's remote lock taken exactly once.
    rm_instance.open_resource.assert_called_once_with(RESOURCE)
    assert resource.write.call_args_list.count(call("SYST:LOCK ON")) == 1

    psu.close()

    # The sink view still holds the box, and is still readable.
    assert call("SYST:LOCK OFF") not in resource.write.call_args_list
    resource.close.assert_not_called()
    # Doubly nested because ``channel_data`` maps channel name to that channel's list of samples.
    assert list(eload.get_voltage(channel=1).channel_data.values()) == [[48.0]]  # type: ignore[union-attr]

    eload.close()

    # Closing the socket before releasing the remote lock would strand a real box in remote-locked
    # state with no session left to unlock it, so the order below is the point of this assertion.
    assert resource.write.call_args_list.count(call("SYST:LOCK OFF")) == 1
    resource.close.assert_called_once()
    assert resource.mock_calls.index(call.write("SYST:LOCK OFF")) < resource.mock_calls.index(call.close())


def test_reopening_the_same_view_is_idempotent(mock_pyvisa):
    _, resource = mock_pyvisa

    box = _Box(RESOURCE)
    box.source.open()
    box.source.open()

    assert resource.write.call_args_list.count(call("SYST:LOCK ON")) == 1


def test_failed_setup_leaves_no_stranded_holder(mock_pyvisa):
    _, resource = mock_pyvisa
    resource.write.side_effect = [RuntimeError("device in local mode"), None, None]

    box = _Box(RESOURCE)
    with pytest.raises(RuntimeError, match="local mode"):
        box.source.open()

    # The retry is first-owner again, so the one-time setup is re-attempted.
    box.source.open()
    assert resource.write.call_args_list.count(call("SYST:LOCK ON")) == 2
