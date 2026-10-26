"""Software tests for the Fluke 8845A driver, which reuses the Agilent 34401A driver's commands."""

from unittest.mock import patch

import pytest

from instro.dmm import InstroDMM, MeasurementFunction
from instro.unstable.dmm.drivers import Fluke8845A


def test_fluke_8845a_drives_the_meter_with_34401a_commands() -> None:
    with patch("instro.dmm.drivers.agilent_a34401a.VisaDriver", autospec=True) as visa_cls:
        visa = visa_cls.return_value
        visa.query.side_effect = lambda cmd: '+0,"No error"' if cmd == "SYST:ERR?" else "+1.50000000E-03"

        dmm = InstroDMM(name="fluke", driver=Fluke8845A("ASRL6::INSTR"))
        dmm.open()
        dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
        reading = dmm.read().latest

    visa_cls.assert_called_once_with("ASRL6::INSTR")
    assert [c.args[0] for c in visa.write.call_args_list] == ["*CLS", "SYST:REM"]
    assert [c.args[0] for c in visa.query.call_args_list if c.args[0] != "SYST:ERR?"] == ["MEAS:VOLT:DC?"] * 2
    assert reading == 1.5e-3


def test_fluke_8845a_errors_name_the_fluke() -> None:
    with patch("instro.dmm.drivers.agilent_a34401a.VisaDriver", autospec=True) as visa_cls:
        visa_cls.return_value.query.return_value = '-113,"Undefined header"'
        driver = Fluke8845A("ASRL6::INSTR")

        with pytest.raises(RuntimeError, match=r'^Fluke 8845A reported error: -113,"Undefined header"$'):
            driver.measure_dc_voltage()
