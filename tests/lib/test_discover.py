import dataclasses
from unittest.mock import MagicMock, patch

import pytest
import pyvisa

from instro.lib.discover import DiscoveredInstrument, VisaInstrumentInfo, match_idn, parse_idn, scan_visa_resources
from instro.lib.registry import DriverEntry
from instro.lib.transports.visa import ControlFlow, Parity, SerialConfig, StopBits, VisaConfig
from instro.psu.config import VisaDriverConfig as PSUVisaDriverConfig
from instro.psu.drivers.bk_9115 import BK9115


def _rm_mock(resources=()):
    mock = MagicMock()
    mock.list_resources.return_value = resources
    return mock


def test_parse_idn_pads_missing_fields() -> None:
    fields = parse_idn("B&K PRECISION, 9115 ")
    assert (fields.manufacturer, fields.model, fields.serial, fields.firmware) == ("B&K PRECISION", "9115", "", "")


@pytest.mark.parametrize(
    "idn,category,driver_name,num_channels",
    [
        # registered but previously missing from discovery's own IDN table
        ("Keysight Technologies,E36103A,MY00000000,1.0", "psu", "KeysightE36100", 1),
        ("Agilent Technologies,N5744A,US00000000,D.00.01", "psu", "KeysightN5700", 1),
        ("LAMBDA,GEN60-25,00000,1.0", "psu", "TDKLambdaGenesys", 1),
        # vendor substring and model regex are case-insensitive
        ("rigol technologies,dp832,DP8C000000000,00.01.14", "psu", "RigolDP800", 3),
        ("B&K PRECISION,8514B,000000000,1.0", "eload", "BK85XXB", None),
    ],
)
def test_match_idn(idn: str, category: str, driver_name: str, num_channels: int | None) -> None:
    match = match_idn(idn)
    assert match is not None
    assert (match.category, match.driver_name, match.num_channels) == (category, driver_name, num_channels)


@pytest.mark.parametrize("idn", ["UNKNOWN VENDOR,XYZ,000,1.0", "RIGOL TECHNOLOGIES,DS1054Z,SN,1.0", ""])
def test_match_idn_unknown(idn: str) -> None:
    assert match_idn(idn) is None


def test_scan_empty_bench() -> None:
    mock_rm = _rm_mock(())
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        result = scan_visa_resources()
    assert result.instruments == []
    assert result.unrecognized == []
    assert result.errors == []


def test_scan_recognized_psu() -> None:
    mock_rm = _rm_mock(("USB0::0x15EF::0x0099::MY001::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = "B&K PRECISION,9115,12345,1.0"
            result = scan_visa_resources()

    assert len(result.instruments) == 1
    assert result.unrecognized == []
    assert result.errors == []
    info = result.instruments[0]
    assert info.resource == "USB0::0x15EF::0x0099::MY001::INSTR"
    assert info.category == "psu"
    assert info.driver_name == "BK9115"
    assert info.num_channels == 1


def test_scan_recognized_siglent_spd3303_reports_two_programmable_channels() -> None:
    # Channel 3 is a front-panel-only switch with no SCPI programming interface
    # (SiglentSPD3303._require_programmable_channel raises for it), so discovery
    # must report 2, not the 3 physical output channels.
    mock_rm = _rm_mock(("USB0::0xF4EC::0x1430::SPD3XJGQ806726::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = "SIGLENT TECHNOLOGIES,SPD3303X,12345,1.0"
            result = scan_visa_resources()

    assert len(result.instruments) == 1
    info = result.instruments[0]
    assert info.driver_name == "SiglentSPD3303"
    assert info.num_channels == 2


def test_scan_recognized_dmm() -> None:
    mock_rm = _rm_mock(("USB0::0x05E6::0x2400::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = "KEITHLEY INSTRUMENTS,2400,12345,C30"
            result = scan_visa_resources()

    assert len(result.instruments) == 1
    info = result.instruments[0]
    assert info.category == "dmm"
    assert info.driver_name == "Keithley2400"
    assert info.num_channels is None


@pytest.mark.parametrize(
    "idn,driver_class",
    [
        ("Keysight Technologies,EDUX1052A,SN,1.0", "Keysight1200X"),
        ("TEKTRONIX,MSO24,C012345,CF:91.1CT FV:1.20", "Tektronix2SeriesMSO"),
        ("Siglent Technologies,SDS1104X-E,SN,1.0", "SiglentSDS1000XE"),
    ],
)
def test_scan_recognized_scope(idn: str, driver_class: str) -> None:
    mock_rm = _rm_mock(("USB0::0x0957::0x1755::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = idn
            result = scan_visa_resources()

    assert len(result.instruments) == 1
    info = result.instruments[0]
    assert info.category == "scope"
    assert info.driver_name == driver_class


@pytest.mark.parametrize(
    "idn,driver_class,num_channels",
    [
        ("Rigol Technologies,DG1022Z,DG1ZA000000000,03.01.12", "RigolDG1022Z", 2),
        ("Rigol Technologies,DG1062Z,DG1ZA000000000,03.01.12", "RigolDG1022Z", 2),
        ("Agilent Technologies,33521B,MY52702203,3.03-1.19-2.00-52-00", "Keysight33521B", 1),
        ("Keysight Technologies,33521B,MY52702203,3.03-1.19-2.00-52-00", "Keysight33521B", 1),
    ],
)
def test_scan_recognized_awg(idn: str, driver_class: str, num_channels: int) -> None:
    mock_rm = _rm_mock(("USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = idn
            result = scan_visa_resources()

    assert len(result.instruments) == 1
    info = result.instruments[0]
    assert info.category == "awg"
    assert info.driver_name == driver_class
    assert info.num_channels == num_channels


def test_scan_unrecognized() -> None:
    mock_rm = _rm_mock(("USB0::0xABCD::0x1234::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = "UNKNOWN VENDOR,XYZ,000,1.0"
            result = scan_visa_resources()

    assert result.instruments == []
    assert len(result.unrecognized) == 1
    assert result.errors == []
    assert result.unrecognized[0].resource == "USB0::0xABCD::0x1234::INSTR"
    assert "UNKNOWN VENDOR" in result.unrecognized[0].idn.upper()


def test_scan_error() -> None:
    mock_rm = _rm_mock(("USB0::0x1234::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.open.side_effect = Exception("timeout")
            result = scan_visa_resources()

    assert result.instruments == []
    assert result.unrecognized == []
    assert len(result.errors) == 1
    assert result.errors[0].resource == "USB0::0x1234::INSTR"
    assert "timeout" in result.errors[0].message
    assert result.errors[0].hint is None


def test_scan_error_classifies_permission_denied_hint() -> None:
    mock_rm = _rm_mock(("USB0::0x1234::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.open.side_effect = pyvisa.errors.VisaIOError(
                pyvisa.constants.StatusCode.error_system_error
            )
            result = scan_visa_resources()

    assert len(result.errors) == 1
    assert "SYSTEM_ERROR" in result.errors[0].message
    assert result.errors[0].hint == "permission denied - check udev rules"


def test_scan_error_classifies_missing_usb_backend_hint() -> None:
    mock_rm = _rm_mock(("USB0::0x1234::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.open.side_effect = Exception("No backend available")
            result = scan_visa_resources()

    assert len(result.errors) == 1
    assert result.errors[0].hint == "USB backend missing - install libusb"


def test_scan_asrl_skipped() -> None:
    mock_rm = _rm_mock(("ASRL1::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            result = scan_visa_resources()

    mock_driver_cls.assert_not_called()
    assert result.instruments == []
    assert result.unrecognized == []
    assert result.errors == []


def test_scan_mixed() -> None:
    resources = (
        "USB0::0x15EF::0x0099::MY001::INSTR",  # BK9115 — recognized PSU
        "USB0::0x05E6::0x2400::INSTR",  # Keithley — recognized DMM
        "USB0::0xABCD::0x1234::INSTR",  # unknown — unrecognized
        "USB0::0xDEAD::0xBEEF::INSTR",  # error
        "ASRL1::INSTR",  # serial — skipped
    )
    mock_rm = _rm_mock(resources)
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.side_effect = [
                "B&K PRECISION,9115,12345,1.0",
                "KEITHLEY INSTRUMENTS,2400,12345,C30",
                "UNKNOWN VENDOR,XYZ,000,1.0",
                Exception("timeout"),
            ]
            result = scan_visa_resources()

    assert len(result.instruments) == 2
    assert len(result.unrecognized) == 1
    assert len(result.errors) == 1
    assert result.instruments[0].category == "psu"
    assert result.instruments[1].category == "dmm"


def test_discovered_instrument_builds_driver_and_config_block() -> None:
    serial = SerialConfig(baud_rate=57600, stop_bits=StopBits.TWO, parity=Parity.EVEN, flow_control=ControlFlow.DTR_DSR)
    found = DiscoveredInstrument(
        resource="ASRL3::INSTR",
        idn="B&K PRECISION,9115,12345,1.0",
        category="psu",
        driver_name="BK9115",
        num_channels=1,
        backend="@py",
        serial_config=serial,
    )
    assert found.driver_class() is BK9115
    cfg = found.visa_config()
    assert (cfg.visa_resource, cfg.visa_backend) == ("ASRL3::INSTR", "@py")
    assert cfg.serial_config == serial
    assert cfg.serial_config is not found.serial_config  # a copy: tweaking the config leaves the record alone
    cfg.serial_config.baud_rate = 9600
    assert found.serial_config.baud_rate == 57600
    assert isinstance(found.make_driver(), BK9115)

    block = found.config_block()
    assert block == {
        "name": "BK9115",
        "num_channels": 1,
        "visa": {
            "visa_resource": "ASRL3::INSTR",
            "visa_backend": "@py",
            "serial_config": {"baud_rate": 57600, "data_bits": 8, "stop_bits": 2, "parity": "E", "flow_control": 4},
        },
    }
    validated = PSUVisaDriverConfig.model_validate(block)
    assert isinstance(validated.visa, VisaConfig)
    assert validated.visa.serial_config == serial


def test_discovered_instrument_is_immutable_and_not_hashable() -> None:
    usb = DiscoveredInstrument(resource="USB0::1::2::INSTR", idn="x", category="psu", driver_name="BK9115")
    serial = dataclasses.replace(usb, resource="ASRL1::INSTR", serial_config=SerialConfig())
    with pytest.raises(dataclasses.FrozenInstanceError):
        usb.resource = "other"  # type: ignore[misc]
    for record in (usb, serial):  # the same answer whether or not serial settings are attached
        with pytest.raises(TypeError):
            hash(record)
    assert usb == dataclasses.replace(usb)


def test_discovered_instrument_without_serial_config_uses_transport_defaults() -> None:
    found = DiscoveredInstrument(
        resource="USB0::1::2::INSTR", idn="B&K PRECISION,9115,1,1.0", category="psu", driver_name="BK9115"
    )
    assert found.visa_config().serial_config == SerialConfig()
    assert "serial_config" not in found.config_block()["visa"]


def test_discovered_instrument_non_visa_transport_passes_resource_to_driver() -> None:
    """A vendor-SDK provider's record resolves through the same registry lookup as a VISA one."""
    found = DiscoveredInstrument(
        resource="Dev1", idn="USB-6002", category="daq", driver_name="FakeDAQ", transport="nidaqmx"
    )
    entry = MagicMock(spec=DriverEntry)
    with patch("instro.lib.discover.driver_registry", return_value={"FakeDAQ": entry}) as registry:
        driver = found.make_driver()
    registry.assert_called_once_with("daq")
    entry.load.return_value.assert_called_once_with("Dev1")
    assert driver is entry.load.return_value.return_value
    with pytest.raises(ValueError, match="not VISA"):
        found.visa_config()
    with pytest.raises(ValueError, match="no JSON config schema"):
        found.config_block()


def test_discovered_instrument_unregistered_driver_name_raises() -> None:
    found = DiscoveredInstrument(resource="USB0::1::2::INSTR", idn="x", category="psu", driver_name="PSUDriverBase")
    with pytest.raises(KeyError, match="not registered|registered for category 'psu'"):
        found.driver_class()


def test_scan_records_requested_backend_not_resolved_one() -> None:
    mock_rm = _rm_mock(("USB0::0x15EF::0x0099::MY001::INSTR",))
    with patch("instro.lib.discover.pyvisa.ResourceManager", return_value=mock_rm):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = "B&K PRECISION,9115,12345,1.0"
            explicit = scan_visa_resources(backend="@py")
    assert explicit.instruments[0].backend == "@py"
    assert explicit.instruments[0].visa_config().visa_backend == "@py"
    assert explicit.instruments[0].config_block()["visa"]["visa_backend"] == "@py"

    # default request that fell back to @py: the record must not pin @py, or the config it
    # produces would skip @ivi on a bench that has it
    with patch("instro.lib.discover.pyvisa.ResourceManager", side_effect=[OSError("no IVI"), mock_rm]):
        with patch("instro.lib.discover.VisaDriver") as mock_driver_cls:
            mock_driver_cls.return_value.query.return_value = "B&K PRECISION,9115,12345,1.0"
            default = scan_visa_resources()
    assert default.instruments[0].backend is None
    assert default.instruments[0].visa_config().visa_backend is None
    assert "visa_backend" not in default.instruments[0].config_block()["visa"]
    assert VisaInstrumentInfo is DiscoveredInstrument
    assert default.instruments[0].driver_class_name == "BK9115"
