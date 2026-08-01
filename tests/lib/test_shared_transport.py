"""One transport shared by two driver surfaces: exactly one session per box, torn down by the last owner.

Given a combined instrument (one box that both sources and sinks, e.g. the EA PSB 10000),
when the user constructs one connection and hands it to a PSU driver and an ELoad driver,
then the two surfaces share that one session for as long as either of them owns it.

Two driver objects rather than one ``EAPSB(PSUDriverBase, ELoadDriverBase)`` because each surface
applies its own current-sign convention and publishes into its own category namespace. The stubs below
carry real SCPI only where the single test reaches them — the lock lifecycle, one measurement, one
output toggle; every other abstract method raises, because this test pins the shared-ownership contract
and the vendor commands belong to the EA drivers' own tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from pyvisa.constants import InterfaceType

from instro.eload import ELoadDriverBase, InstroELoad, LoadMode, SlewRateDirection
from instro.lib.transports import VisaDriver
from instro.psu import InstroPSU, PSUDriverBase

RESOURCE = "TCPIP0::10.0.0.5::5025::SOCKET"


class _SharedPSUDriver(PSUDriverBase):
    """Mirrors the EA PSB lifecycle: takes the device's remote lock when it is the connection's first owner."""

    def __init__(self, connection: VisaDriver) -> None:
        self._visa = connection

    def open(self) -> None:
        if self._visa.acquire(self):
            try:
                self._visa.write("SYST:LOCK ON")
            except Exception:
                # Releasing here is the driver's obligation: a stranded holder would make the retry's
                # acquire report not-first-owner, so SYST:LOCK ON would never be re-attempted.
                self._visa.release(self)
                raise

    def close(self) -> None:
        self._visa.release(self, on_last_release=lambda: self._visa.write("SYST:LOCK OFF"))

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


class _SharedELoadDriver(ELoadDriverBase):
    """The sink surface of the same box; same lifecycle as ``_SharedPSUDriver``, deliberately mirrored."""

    def __init__(self, connection: VisaDriver) -> None:
        self._visa = connection

    def open(self) -> None:
        if self._visa.acquire(self):
            try:
                self._visa.write("SYST:LOCK ON")
            except Exception:
                self._visa.release(self)
                raise

    def close(self) -> None:
        self._visa.release(self, on_last_release=lambda: self._visa.write("SYST:LOCK OFF"))

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
        return float(self._visa.query("MEAS:VOLT?"))


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


def test_psu_and_eload_surfaces_share_one_session(mock_pyvisa):
    rm_instance, resource = mock_pyvisa

    connection = VisaDriver(RESOURCE)
    psu = InstroPSU(name="psb_source", driver=_SharedPSUDriver(connection), num_channels=1)
    eload = InstroELoad(name="psb_sink", driver=_SharedELoadDriver(connection))

    psu.open()
    eload.open()

    # One socket for the box, and the device's remote lock taken exactly once.
    rm_instance.open_resource.assert_called_once_with(RESOURCE)
    assert resource.write.call_args_list.count(call("SYST:LOCK ON")) == 1

    psu.close()

    # The ELoad still holds the box, and is still readable.
    assert call("SYST:LOCK OFF") not in resource.write.call_args_list
    resource.close.assert_not_called()
    assert connection.is_open is True
    # Doubly nested because ``channel_data`` maps channel name to that channel's list of samples.
    assert list(eload.get_voltage(channel=1).channel_data.values()) == [[48.0]]  # type: ignore[union-attr]

    eload.close()

    # Closing the socket before releasing the remote lock would strand a real box in remote-locked
    # state with no session left to unlock it, so the order below is the point of this assertion.
    assert resource.write.call_args_list.count(call("SYST:LOCK OFF")) == 1
    resource.close.assert_called_once()
    assert resource.mock_calls.index(call.write("SYST:LOCK OFF")) < resource.mock_calls.index(call.close())
    assert connection.is_open is False
