from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from instro.cli.main import app

runner = CliRunner()


# Test 1 — empty bench:
def test_discover_empty_bench():
    with patch("instro.cli.discover.pyvisa.ResourceManager") as mock_rm:
        mock_rm.return_value.list_resources.return_value = ()
        result = runner.invoke(app, ["discover"])
    print(result.output)
    assert result.exit_code == 0


# Test 2 — mixed bench (one known, one unknown):
def test_discover_mixed_bench():
    mock_resource = MagicMock()
    mock_resource.query.return_value = "KEITHLEY INSTRUMENTS,MODEL 2400,12345,C30"

    with patch("instro.cli.discover.pyvisa.ResourceManager") as mock_rm:
        with patch("instro.cli.discover.VisaDriver") as mock_driver_cls:
            mock_rm.return_value.list_resources.return_value = (
                "USB0::0x05E6::0x2400::INSTR",
                "USB0::0xABCD::0x9999::INSTR",
            )
            # first resource returns a known IDN, second raises
            mock_driver_cls.return_value.query.side_effect = [
                "KEITHLEY INSTRUMENTS,MODEL 2400,12345,C30",
                "UNKNOWN VENDOR,XYZ,000,1.0",
            ]
            result = runner.invoke(app, ["discover"])
    print(result.output)

    assert "SUPPORTED" in result.output
    assert "UNSUPPORTED" in result.output


# Test 3 — failed probe:
def test_discover_failed_probe():
    with patch("instro.cli.discover.pyvisa.ResourceManager") as mock_rm:
        with patch("instro.cli.discover.VisaDriver") as mock_driver_cls:
            mock_rm.return_value.list_resources.return_value = ("USB0::0x1234::INSTR",)
            mock_driver_cls.return_value.open.side_effect = Exception("timeout")
            result = runner.invoke(app, ["discover"])
    print(result.output)

    assert result.exit_code == 0  # should not crash
