"""Tests for the DMM driver shape.

Covers InstroDMM delegating to its driver via per-function dispatch. Per-vendor
driver software tests live under tests/dmm/<vendor>/ (e.g.
tests/dmm/agilent/test_agilent_34401a_software.py,
tests/dmm/keithley/test_keithley_2400_software.py).
"""

import threading
from dataclasses import replace
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from instro.dmm import DMMDriverBase, InstroDMM
from instro.dmm import dmm as dmm_module
from instro.dmm.types import MeasurementFunction
from instro.lib import Measurement

# --- InstroDMM composition tests ---


class _StubDMMDriver(DMMDriverBase):
    """Minimal DMMDriverBase implementation for testing InstroDMM behavior."""

    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.last_function: MeasurementFunction | None = None
        self.last_nplc_call: tuple[str, float] | None = None
        self.last_range_call: tuple[str, float | None] | None = None
        self.last_digits: int | None = None
        self.last_aperture_seconds: float | None = None
        self.measured = 0.0

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def set_measurement_function(self, function: MeasurementFunction) -> None:
        self.last_function = function

    def set_digits(self, n: int) -> None:
        self.last_digits = n

    def set_aperture_seconds(self, seconds: float) -> None:
        self.last_aperture_seconds = seconds

    # NPLC overrides — record (method_name, nplc).
    def set_dc_voltage_nplc(self, nplc: float) -> None:
        self.last_nplc_call = ("dc_voltage", nplc)

    def set_dc_current_nplc(self, nplc: float) -> None:
        self.last_nplc_call = ("dc_current", nplc)

    def set_two_wire_resistance_nplc(self, nplc: float) -> None:
        self.last_nplc_call = ("two_wire_resistance", nplc)

    # Range overrides — record (method_name, value).
    def set_dc_voltage_range(self, value: float | None) -> None:
        self.last_range_call = ("dc_voltage", value)

    def set_two_wire_resistance_range(self, value: float | None) -> None:
        self.last_range_call = ("two_wire_resistance", value)

    def measure_dc_voltage(self) -> float:
        return self.measured

    def measure_ac_voltage(self) -> float:
        return self.measured

    def measure_resistance(self) -> float:
        return self.measured

    def measure_dc_current(self) -> float:
        return self.measured

    def measure_ac_current(self) -> float:
        return self.measured


@pytest.fixture
def stub_driver() -> _StubDMMDriver:
    return _StubDMMDriver()


@pytest.fixture
def unconfigured_dmm(stub_driver: _StubDMMDriver) -> InstroDMM:
    return InstroDMM(name="test_dmm", driver=stub_driver)


@pytest.mark.parametrize(
    "action",
    [
        lambda d: d.start(),
        lambda d: d.read(),
        lambda d: d.set_digits(5),
        lambda d: d.set_aperture_seconds(0.1),
        lambda d: d.set_aperture_nplc(1.0),
        lambda d: d.set_range(None),
    ],
)
def test_unconfigured_dmm_raises_value_error(unconfigured_dmm: InstroDMM, action: Callable[[InstroDMM], Any]) -> None:
    with pytest.raises(ValueError, match="set_measurement_function"):
        action(unconfigured_dmm)


def test_nominal_dmm_stores_driver(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    assert dmm._driver is stub_driver


def test_nominal_dmm_open_close_delegate(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.open()
    assert stub_driver.opened
    dmm.close()
    assert stub_driver.closed


def test_nominal_dmm_close_stops_background_before_closing_driver(stub_driver: _StubDMMDriver) -> None:
    events: list[str] = []
    original_close = stub_driver.close

    def record_close() -> None:
        events.append("driver.close")
        original_close()

    stub_driver.close = record_close  # type: ignore[method-assign]
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.stop = MagicMock(side_effect=lambda: events.append("stop"))  # type: ignore[method-assign]

    dmm.close()

    assert events == ["stop", "driver.close"]


def test_nominal_dmm_set_measurement_function_delegates(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    assert stub_driver.last_function is MeasurementFunction.DC_VOLTAGE


def test_nominal_dmm_set_measurement_function_keeps_config_when_driver_rejects(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)

    stub_driver.set_measurement_function = MagicMock(  # type: ignore[method-assign]
        side_effect=NotImplementedError("unsupported")
    )
    with pytest.raises(NotImplementedError):
        dmm.set_measurement_function(MeasurementFunction.AC_VOLTAGE)

    # The rejected function must not be recorded: config still reflects the hardware.
    assert dmm._measurement_config is not None
    assert dmm._measurement_config.function is MeasurementFunction.DC_VOLTAGE


def test_nominal_dmm_first_set_measurement_function_not_recorded_when_driver_rejects(
    stub_driver: _StubDMMDriver,
) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    stub_driver.set_measurement_function = MagicMock(  # type: ignore[method-assign]
        side_effect=NotImplementedError("unsupported")
    )
    with pytest.raises(NotImplementedError):
        dmm.set_measurement_function(MeasurementFunction.AC_VOLTAGE)

    assert dmm._measurement_config is None


def test_nominal_dmm_set_aperture_nplc_dispatches_to_function_method(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_CURRENT)
    dmm.set_aperture_nplc(2.5)
    assert stub_driver.last_nplc_call == ("dc_current", 2.5)


def test_nominal_dmm_set_range_dispatches_to_function_method(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.TWO_WIRE_RESISTANCE)
    dmm.set_range(1000.0)
    assert stub_driver.last_range_call == ("two_wire_resistance", 1000.0)


def test_nominal_dmm_read_returns_measurement(stub_driver: _StubDMMDriver) -> None:
    stub_driver.measured = 3.3
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    measurement = dmm.read()
    assert "ut.dc_voltage" in measurement.channel_data
    assert measurement.channel_data["ut.dc_voltage"] == [3.3]


def test_read_helper_selects_function_then_reads(stub_driver: _StubDMMDriver) -> None:
    stub_driver.measured = 1.5
    dmm = InstroDMM(name="ut", driver=stub_driver)
    measurement = dmm.read_dc_voltage()
    assert stub_driver.last_function is MeasurementFunction.DC_VOLTAGE
    assert measurement.channel_data["ut.dc_voltage"] == [1.5]


def test_read_helper_skips_redundant_function_change(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    stub_driver.set_measurement_function = MagicMock(  # type: ignore[method-assign]
        wraps=stub_driver.set_measurement_function
    )

    dmm.read_dc_voltage()
    dmm.read_dc_voltage()
    assert stub_driver.set_measurement_function.call_count == 1

    dmm.read_resistance()
    assert stub_driver.set_measurement_function.call_count == 2
    assert stub_driver.set_measurement_function.call_args.args == (MeasurementFunction.TWO_WIRE_RESISTANCE,)


def test_read_helper_publishes_select_and_read_outside_the_lock(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    published: list[str] = []

    class _LockCheckingPublisher:
        def publish(self, data: Any, **kwargs: Any) -> None:
            # Publisher I/O must not extend the hold that keeps the daemon waiting.
            assert not dmm._resource_lock.locked()
            published.extend(data.channel_data)

        def close(self) -> None: ...

    dmm.add_publisher(_LockCheckingPublisher())
    dmm.read_dc_voltage()

    assert published == ["ut.set_measurement_function.cmd", "ut.dc_voltage"]


def test_read_helper_is_atomic_against_concurrent_function_change(stub_driver: _StubDMMDriver) -> None:
    """A competing set_measurement_function must not switch the function mid-sequence."""
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    stub_driver.measured = 1.0

    parked = threading.Event()
    resume = threading.Event()

    def stalled_measure() -> float:
        # Park mid-read, after the function check: an implementation that released the
        # lock between the check and the read would let the switcher in here.
        parked.set()
        resume.wait(timeout=5)
        return stub_driver.measured

    stub_driver.measure_dc_voltage = stalled_measure  # type: ignore[method-assign]

    readings: list[Measurement] = []
    reader = threading.Thread(target=lambda: readings.append(dmm.read_dc_voltage()))
    reader.start()
    assert parked.wait(timeout=5)

    switcher = threading.Thread(target=lambda: dmm.set_measurement_function(MeasurementFunction.TWO_WIRE_RESISTANCE))
    switcher.start()
    switcher.join(timeout=0.2)  # blocked on the resource lock; ample time to win the race if it were not
    resume.set()

    reader.join(timeout=5)
    switcher.join(timeout=5)

    assert "ut.dc_voltage" in readings[0].channel_data


# --- _measurement_config access under the resource lock ---


@pytest.mark.parametrize(
    "action",
    [
        lambda d: d.read(),
        lambda d: d.set_digits(5),
        lambda d: d.set_aperture_seconds(0.1),
        lambda d: d.set_aperture_nplc(1.0),
        lambda d: d.set_range(None),
    ],
)
def test_unconfigured_access_checks_the_config_under_the_lock(
    unconfigured_dmm: InstroDMM, action: Callable[[InstroDMM], Any]
) -> None:
    """The ``is None`` precondition must share the critical section with the state it guards."""
    errors: list[str] = []

    def call() -> None:
        try:
            action(unconfigured_dmm)
        except ValueError as exc:
            errors.append(str(exc))

    unconfigured_dmm._resource_lock.acquire()
    caller = threading.Thread(target=call)
    caller.start()
    try:
        caller.join(timeout=0.2)
        # A check taken before the lock raises here instead of waiting for it.
        assert caller.is_alive()
        assert errors == []
    finally:
        unconfigured_dmm._resource_lock.release()

    caller.join(timeout=5)
    assert len(errors) == 1
    assert "set_measurement_function" in errors[0]


@pytest.mark.parametrize(
    "action",
    [
        lambda d: d.set_digits(5),
        lambda d: d.set_aperture_seconds(0.1),
        lambda d: d.set_aperture_nplc(2.0),
        lambda d: d.set_range(10.0),
    ],
)
def test_setter_replaces_the_config_under_the_lock(
    stub_driver: _StubDMMDriver, monkeypatch: pytest.MonkeyPatch, action: Callable[[InstroDMM], Any]
) -> None:
    """The copy-on-write replace must be inside the lock, or concurrent setters drop fields."""
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)

    lock_held: list[bool] = []
    real_replace = dmm_module.replace

    def recording_replace(config: Any, **changes: Any) -> Any:
        lock_held.append(dmm._resource_lock.locked())
        return real_replace(config, **changes)

    monkeypatch.setattr(dmm_module, "replace", recording_replace)
    action(dmm)

    assert lock_held == [True]


def test_read_publishes_the_function_it_dispatched_on(stub_driver: _StubDMMDriver) -> None:
    """read() must not re-read the config after dropping the lock to name the channel."""
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    stub_driver.measured = 1.0

    def measure_after_function_switch() -> float:
        # A concurrent set_measurement_function can land the instant read() drops the lock.
        # Switching here forces that ordering rather than relying on the scheduler.
        config = dmm._measurement_config
        assert config is not None
        dmm._measurement_config = replace(config, function=MeasurementFunction.AC_CURRENT)
        return stub_driver.measured

    stub_driver.measure_dc_voltage = measure_after_function_switch  # type: ignore[method-assign]
    measurement = dmm.read()

    assert "ut.dc_voltage" in measurement.channel_data
