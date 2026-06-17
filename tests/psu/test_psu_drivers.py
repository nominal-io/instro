"""Tests for PSU drivers (driver-owned VisaDriver transport) and InstroPSU composition."""

from unittest.mock import MagicMock

import pytest

from instro.psu import InstroPSU, PSUDriverBase

# --- PSUDriverBase ---


class _BaseOnlyPSUDriver(PSUDriverBase):
    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_voltage(self, voltage: float, channel: int) -> None:
        pass

    def get_voltage(self, channel: int) -> float:
        return 0.0

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        pass

    def get_current(self, channel: int) -> float:
        return 0.0

    def output_enable(self, enable: bool, channel: int) -> None:
        pass

    def get_output_status(self, channel: int) -> bool:
        return False


@pytest.fixture
def base_only_psu_driver() -> _BaseOnlyPSUDriver:
    return _BaseOnlyPSUDriver()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overvoltage_protection_level", (12.0,)),
        ("get_overvoltage_protection_level", ()),
        ("set_overvoltage_protection_enabled", (True,)),
        ("get_overvoltage_protection_enabled", ()),
        ("set_overvoltage_protection_delay", (0.25,)),
        ("get_overvoltage_protection_delay", ()),
    ],
)
def test_psu_driver_base_ovp_methods_raise_not_implemented(
    base_only_psu_driver: _BaseOnlyPSUDriver,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for _BaseOnlyPSUDriver"):
        getattr(base_only_psu_driver, method_name)(*args, channel=1)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overcurrent_protection_level", (1.0,)),
        ("get_overcurrent_protection_level", ()),
        ("set_overcurrent_protection_enabled", (True,)),
        ("get_overcurrent_protection_enabled", ()),
    ],
)
def test_psu_driver_base_ocp_methods_raise_not_implemented(
    base_only_psu_driver: _BaseOnlyPSUDriver,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for _BaseOnlyPSUDriver"):
        getattr(base_only_psu_driver, method_name)(*args, channel=1)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_remote_sense_enabled", (True,)),
        ("get_remote_sense_enabled", ()),
    ],
)
def test_psu_driver_base_remote_sense_methods_raise_not_implemented(
    base_only_psu_driver: _BaseOnlyPSUDriver,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for _BaseOnlyPSUDriver"):
        getattr(base_only_psu_driver, method_name)(*args, channel=1)


# --- InstroPSU composition ---


def _stub_driver() -> MagicMock:
    driver = MagicMock(spec=PSUDriverBase)
    driver.get_voltage.return_value = 12.0
    driver.get_current.return_value = 0.5
    driver.get_output_status.return_value = True
    driver.get_overvoltage_protection_level.return_value = 15.0
    driver.get_overvoltage_protection_enabled.return_value = True
    driver.get_overvoltage_protection_delay.return_value = 0.25
    driver.get_overcurrent_protection_level.return_value = 2.0
    driver.get_overcurrent_protection_enabled.return_value = True
    driver.get_remote_sense_enabled.return_value = True
    return driver


def test_nominal_psu_stores_driver() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    assert psu._driver is driver


def test_nominal_psu_open_close_delegate_to_driver() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    psu.open()
    driver.open.assert_called_once()
    psu.close()
    driver.close.assert_called_once()


def test_nominal_psu_close_stops_background_before_closing_driver() -> None:
    events: list[str] = []
    driver = _stub_driver()
    driver.close.side_effect = lambda: events.append("driver.close")
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    psu.stop = MagicMock(side_effect=lambda: events.append("stop"))  # type: ignore[method-assign]

    psu.close()

    assert events == ["stop", "driver.close"]


def test_nominal_psu_set_voltage_delegates() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=2)
    psu.set_voltage(5.0, channel=2)
    driver.set_voltage.assert_called_once_with(5.0, channel=2)


def test_nominal_psu_get_voltage_returns_measurement() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    measurement = psu.get_voltage(channel=1)
    assert measurement is not None
    assert "ut.ch1.voltage" in measurement.channel_data
    assert measurement.channel_data["ut.ch1.voltage"] == [12.0]


def test_nominal_psu_get_current_returns_measurement() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    measurement = psu.get_current(channel=1)
    assert measurement is not None
    assert "ut.ch1.current" in measurement.channel_data
    assert measurement.channel_data["ut.ch1.current"] == [0.5]


def test_nominal_psu_output_enable_delegates() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    psu.output_enable(True, channel=1)
    driver.output_enable.assert_called_once_with(True, channel=1)


def test_nominal_psu_set_current_limit_delegates() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    psu.set_current_limit(1.5, channel=1)
    driver.set_current_limit.assert_called_once_with(1.5, channel=1)


def test_nominal_psu_ovp_methods_delegate_and_package() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)

    level_cmd = psu.set_overvoltage_protection_level(15.0, channel=1)
    level = psu.get_overvoltage_protection_level(channel=1)
    enabled_cmd = psu.set_overvoltage_protection_enabled(True, channel=1)
    enabled = psu.get_overvoltage_protection_enabled(channel=1)
    delay_cmd = psu.set_overvoltage_protection_delay(0.25, channel=1)
    delay = psu.get_overvoltage_protection_delay(channel=1)

    driver.set_overvoltage_protection_level.assert_called_once_with(15.0, channel=1)
    driver.get_overvoltage_protection_level.assert_called_once_with(channel=1)
    driver.set_overvoltage_protection_enabled.assert_called_once_with(True, channel=1)
    driver.get_overvoltage_protection_enabled.assert_called_once_with(channel=1)
    driver.set_overvoltage_protection_delay.assert_called_once_with(0.25, channel=1)
    driver.get_overvoltage_protection_delay.assert_called_once_with(channel=1)
    assert "ut.ch1.ovp.cmd" in level_cmd.channel_data
    assert "ut.ch1.ovp" in level.channel_data  # type: ignore[union-attr]
    assert "ut.ch1.ovp.enabled.cmd" in enabled_cmd.channel_data
    assert "ut.ch1.ovp.enabled" in enabled.channel_data  # type: ignore[union-attr]
    assert "ut.ch1.ovp.delay.cmd" in delay_cmd.channel_data
    assert "ut.ch1.ovp.delay" in delay.channel_data  # type: ignore[union-attr]


def test_nominal_psu_ocp_methods_delegate_and_package() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)

    level_cmd = psu.set_overcurrent_protection_level(2.0, channel=1)
    level = psu.get_overcurrent_protection_level(channel=1)
    enabled_cmd = psu.set_overcurrent_protection_enabled(True, channel=1)
    enabled = psu.get_overcurrent_protection_enabled(channel=1)

    driver.set_overcurrent_protection_level.assert_called_once_with(2.0, channel=1)
    driver.get_overcurrent_protection_level.assert_called_once_with(channel=1)
    driver.set_overcurrent_protection_enabled.assert_called_once_with(True, channel=1)
    driver.get_overcurrent_protection_enabled.assert_called_once_with(channel=1)
    assert "ut.ch1.ocp.cmd" in level_cmd.channel_data
    assert "ut.ch1.ocp" in level.channel_data  # type: ignore[union-attr]
    assert "ut.ch1.ocp.enabled.cmd" in enabled_cmd.channel_data
    assert "ut.ch1.ocp.enabled" in enabled.channel_data  # type: ignore[union-attr]


def test_nominal_psu_remote_sense_methods_delegate_and_package() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)

    enabled_cmd = psu.set_remote_sense_enabled(True, channel=1)
    enabled = psu.get_remote_sense_enabled(channel=1)

    driver.set_remote_sense_enabled.assert_called_once_with(True, channel=1)
    driver.get_remote_sense_enabled.assert_called_once_with(channel=1)
    assert "ut.ch1.remote_sense.cmd" in enabled_cmd.channel_data
    assert "ut.ch1.remote_sense" in enabled.channel_data  # type: ignore[union-attr]


# --- legacy_naming ---


def test_legacy_naming_publishes_old_psu_channel_names() -> None:
    """`legacy_naming=True` round-trips pre-v1.0 PSU channel names."""
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=2, legacy_naming=True)

    voltage = psu.get_voltage(channel=1)
    current = psu.get_current(channel=1)
    enabled = psu.get_output_status(channel=2)
    voltage_cmd = psu.set_voltage(5.0, channel=1)
    current_cmd = psu.set_current_limit(1.5, channel=1)
    enabled_cmd = psu.output_enable(True, channel=2)

    assert "ut.ch1_v" in voltage.channel_data  # type: ignore[union-attr]
    assert "ut.ch1_i" in current.channel_data  # type: ignore[union-attr]
    assert "ut.ch2_en" in enabled.channel_data  # type: ignore[union-attr]
    assert "ut.ch1_v.cmd" in voltage_cmd.channel_data
    assert "ut.ch1_i.cmd" in current_cmd.channel_data
    assert "ut.ch2_en.cmd" in enabled_cmd.channel_data


def test_default_naming_publishes_new_psu_channel_names() -> None:
    """Default (`legacy_naming=False`) publishes the v1.0 descriptive channel names."""
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)

    assert "ut.ch1.voltage" in psu.get_voltage(channel=1).channel_data  # type: ignore[union-attr]
    assert "ut.ch1.voltage.cmd" in psu.set_voltage(5.0, channel=1).channel_data


def test_legacy_naming_default_is_false() -> None:
    driver = _stub_driver()
    psu = InstroPSU(name="ut", driver=driver, num_channels=1)
    assert psu.legacy_naming is False


# --- Publish decorators: type-check invariant ---


def test_publish_command_rejects_method_returning_measurement() -> None:
    """@publish_command raises TypeError when the wrapped method returns a Measurement."""
    from instro.lib import Measurement
    from instro.lib.instrument import publish_command

    class _Bad(InstroPSU):
        @publish_command
        def bad(self) -> Measurement:  # type: ignore[override]
            return Measurement(channel_data={"ut.x": [1.0]}, timestamps=[0])

    inst = _Bad(name="ut", driver=_stub_driver(), num_channels=1)
    with pytest.raises(TypeError, match="must return Command"):
        inst.bad()


def test_publish_measurement_rejects_method_returning_command() -> None:
    """@publish_measurement raises TypeError when the wrapped method returns a Command."""
    from instro.lib import Command
    from instro.lib.instrument import publish_measurement

    class _Bad(InstroPSU):
        @publish_measurement
        def bad(self) -> Command:  # type: ignore[override]
            return Command(channel_data={"ut.x.cmd": 1.0}, timestamp=0)

    inst = _Bad(name="ut", driver=_stub_driver(), num_channels=1)
    with pytest.raises(TypeError, match="must return Measurement"):
        inst.bad()


def test_publish_measurement_passes_through_none() -> None:
    """@publish_measurement returns None without publishing when the method returns None."""
    from instro.lib.instrument import publish_measurement

    class _Quiet(InstroPSU):
        @publish_measurement
        def quiet(self) -> None:
            return None

    inst = _Quiet(name="ut", driver=_stub_driver(), num_channels=1)
    assert inst.quiet() is None
