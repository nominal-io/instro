"""Feature test for the ESPEC GL chamber driver (INSTRO-499).

Encodes the primary user story end-to-end: open the controller, query its
identity, read the current temperature, set a new temperature setpoint, and
close. Measurements and commands are published to configured publishers.
Fails until the driver module exists.
"""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.lib.publishers import Publisher
from instro.lib.transports.visa import VisaConfig
from instro.lib.types import Command, Measurement
from instro.unstable.chamber.drivers import EspecGL

# Canned controller replies keyed by the exact wire command (spec §5).
_REPLIES = {
    "ROM?": "GL-ENA 3.4.0",
    "TYPE?": "T,T,GL,185.0",
    "TEMP?": "23.0,85.0,105.0,-75.0",
    "TEMP,S85.0": "OK:1,temp,s85.0",
}


@pytest.fixture
def espec_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.chamber.drivers.espec_gl.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def espec_visa(espec_visa_cls: MagicMock) -> MagicMock:
    visa = espec_visa_cls.return_value
    visa.query.side_effect = lambda command: _REPLIES[command]
    return visa


@pytest.fixture
def mock_publisher() -> MagicMock:
    return MagicMock(spec=Publisher)


def _queries(visa: MagicMock) -> list[str]:
    return [c.args[0] for c in visa.query.call_args_list]


def test_open_identify_read_set_close(
    espec_visa_cls: MagicMock, espec_visa: MagicMock, mock_publisher: MagicMock
) -> None:
    """User story: open chamber, identify, read temperature, set setpoint, close.

    Measurements and commands are published to configured publishers.
    """
    chamber = EspecGL("TCPIP0::192.168.0.83::10001::SOCKET", name="chamber_1", publishers=[mock_publisher])

    chamber.open()
    espec_visa.open.assert_called_once_with()

    # identify() emits a measurement and returns the identity string
    identity = chamber.identify()
    assert "GL-ENA 3.4.0" in identity.latest

    # get_temperature() emits a measurement
    temperature = chamber.get_temperature()
    assert temperature.latest == pytest.approx(23.0)

    # set_temperature_setpoint() emits a command
    chamber.set_temperature_setpoint(85.0)

    # Verify the publisher received the expected measurements and command
    published = [c.args[0] for c in mock_publisher.publish.call_args_list]
    assert sum(isinstance(p, Measurement) for p in published) >= 2  # identify + get_temperature
    assert any(isinstance(p, Command) for p in published)  # set_temperature_setpoint

    chamber.close()
    espec_visa.close.assert_called_once_with()

    # Verify all expected wire commands were sent
    queries = _queries(espec_visa)
    assert "ROM?" in queries
    assert "TYPE?" in queries
    assert "TEMP?" in queries
    assert "TEMP,S85.0" in queries


def test_open_close_delegate(espec_visa_cls: MagicMock, espec_visa: MagicMock) -> None:
    chamber = EspecGL("TCPIP0::1.2.3.4::10001::SOCKET", name="c")
    espec_visa_cls.assert_called_once_with("TCPIP0::1.2.3.4::10001::SOCKET")

    chamber.open()
    espec_visa.open.assert_called_once_with()

    chamber.close()
    espec_visa.close.assert_called_once_with()


def test_init_accepts_visa_config(espec_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="ASRL/dev/ttyUSB0::INSTR")
    EspecGL(config, name="c")
    espec_visa_cls.assert_called_once_with(config)


def test_monitor_and_command_pass_through_on_success(espec_visa_cls: MagicMock, espec_visa: MagicMock) -> None:
    chamber = EspecGL("TCPIP0::1.2.3.4::10001::SOCKET", name="c")

    espec_visa.query.side_effect = None
    espec_visa.query.return_value = "23.0,85.0,105.0,-75.0"
    assert chamber._monitor("TEMP?") == "23.0,85.0,105.0,-75.0"

    espec_visa.query.return_value = "OK:1,temp,s85.0"
    assert chamber._command("TEMP,S85.0") == "OK:1,temp,s85.0"


@pytest.mark.parametrize("reply", ["NA:PROTECT ON", "NA :INVLID REQ"])
def test_monitor_raises_on_na_prefix(espec_visa_cls: MagicMock, espec_visa: MagicMock, reply: str) -> None:
    chamber = EspecGL("TCPIP0::1.2.3.4::10001::SOCKET", name="c")
    espec_visa.query.side_effect = None
    espec_visa.query.return_value = reply

    with pytest.raises(RuntimeError, match=reply.replace(":", r"\:")):
        chamber._monitor("HUMI?")


def test_command_raises_unless_ok_prefix(espec_visa_cls: MagicMock, espec_visa: MagicMock) -> None:
    chamber = EspecGL("TCPIP0::1.2.3.4::10001::SOCKET", name="c")
    espec_visa.query.side_effect = None
    espec_visa.query.return_value = "NA:DATA OUT OF RANGE"

    with pytest.raises(RuntimeError, match="NA:DATA OUT OF RANGE"):
        chamber._command("TEMP,S9999.0")
