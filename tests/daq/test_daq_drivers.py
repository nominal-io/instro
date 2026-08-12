"""Unit tests for DAQ driver functionality."""

import logging
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from instro.daq import DAQDriverBase, InstroDAQ, TimingConfigException
from instro.daq.drivers import HWTimestamper
from instro.daq.types import (
    DigitalLineChannel,
    DigitalPortChannel,
    DigitalPortWidth,
    Direction,
    Logic,
)
from instro.daq.units import parse_unit
from instro.lib import InstrumentNotOpenError, Measurement


class _RecordingDriver(DAQDriverBase):
    """Concrete driver for ``InstroDAQ`` boundary tests.

    ``configure_*`` record real frozen channels on the private dicts (matching the real-driver
    contract), so the read-only ``@property`` snapshots behave exactly as they do in production.
    The action methods are per-instance ``Mock``s so tests can assert calls and set return values.
    """

    # Concrete bodies clear the abstractmethod flags; __init__ shadows these with per-instance Mocks.
    def open(self): ...
    def close(self): ...
    def start(self, **kwargs): ...
    def stop(self, **kwargs): ...
    def read_analog(self): ...
    def fetch_analog(self): ...
    def write_digital_line(self, channel, data): ...
    def read_digital_line(self, channel): ...
    def write_digital_port(self, channel, data): ...
    def read_digital_port(self, channel): ...
    def _read_to_measurements(self, response, channel_list, daq_name, default_tags, **kwargs): ...

    _ACTION_METHODS = (
        "open",
        "close",
        "start",
        "stop",
        "read_analog",
        "fetch_analog",
        "write_analog_value",
        "write_digital_line",
        "read_digital_line",
        "write_digital_port",
        "read_digital_port",
        "close_relay",
        "open_relay",
        "_read_to_measurements",
    )

    def __init__(self) -> None:
        super().__init__()
        for name in self._ACTION_METHODS:
            setattr(self, name, Mock(name=name))

    def configure_ai_channel(self, channel):
        self._ai_channels[channel.alias] = channel

    def configure_ao_channel(self, channel):
        self._ao_channels[channel.alias] = channel

    def configure_ai_voltage_channel(self, channel):
        self._ai_channels[channel.alias] = channel

    def configure_ao_voltage_channel(self, channel):
        self._ao_channels[channel.alias] = channel

    def configure_ai_current_channel(self, channel):
        self._ai_channels[channel.alias] = channel

    def configure_ao_current_channel(self, channel):
        self._ao_channels[channel.alias] = channel

    def configure_ai_thermocouple_channel(self, channel):
        self._ai_channels[channel.alias] = channel

    def configure_di_channel(self, channel):
        self._di_channels[channel.alias] = channel

    def configure_do_channel(self, channel):
        self._do_channels[channel.alias] = channel

    def configure_ai_hw_timing(self, hw_timing_config):
        self._ai_hw_timing_config = hw_timing_config

    def configure_di_line_channel(self, physical_channel, logic, logic_level=None, alias=None):
        key = alias or physical_channel
        self._di_channels[key] = DigitalLineChannel(
            physical_channel=physical_channel,
            alias=key,
            direction=Direction.INPUT,
            logic_level=logic_level,
            logic=logic,
        )

    def configure_do_line_channel(self, physical_channel, logic, logic_level=None, alias=None):
        key = alias or physical_channel
        self._do_channels[key] = DigitalLineChannel(
            physical_channel=physical_channel,
            alias=key,
            direction=Direction.OUTPUT,
            logic_level=logic_level,
            logic=logic,
        )

    def configure_di_port_channel(self, physical_channel, logic, port_width, logic_level=None, alias=None):
        key = alias or physical_channel
        self._di_channels[key] = DigitalPortChannel(
            physical_channel=physical_channel,
            alias=key,
            direction=Direction.INPUT,
            logic_level=logic_level,
            logic=logic,
            width=DigitalPortWidth(port_width),
        )

    def configure_do_port_channel(self, physical_channel, logic, port_width, logic_level=None, alias=None):
        key = alias or physical_channel
        self._do_channels[key] = DigitalPortChannel(
            physical_channel=physical_channel,
            alias=key,
            direction=Direction.OUTPUT,
            logic_level=logic_level,
            logic=logic,
            width=DigitalPortWidth(port_width),
        )


def _make_mock_driver() -> _RecordingDriver:
    """A concrete ``DAQDriverBase`` whose ``configure_*`` record real channels and whose action methods are Mocks."""
    return _RecordingDriver()


def test_write_digital_line_configured_channel():
    """Test that writing to a configured channel works without error."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    daq.configure_digital_line(
        direction=Direction.OUTPUT, physical_channel="port0/line0", logic=Logic.HIGH, alias="test_channel"
    )

    daq.write_digital_line("test_channel", 1)

    mock_driver.write_digital_line.assert_called_once()


def test_write_digital_line_unconfigured_channel():
    """Test that writing to an unconfigured channel raises an error."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    with pytest.raises(KeyError, match="Digital output channel 'unconfigured_channel' is not configured") as exc_info:
        daq.write_digital_line("unconfigured_channel", 1)

    print(f"\nRaised error: {exc_info.value}")

    mock_driver.write_digital_line.assert_not_called()


def test_read_digital_line_configured_channel():
    """Test that reading from a configured channel works without error."""
    mock_driver = _make_mock_driver()
    mock_driver.read_digital_line.return_value = 1

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    daq.configure_digital_line(
        direction=Direction.INPUT, physical_channel="port0/line0", alias="test_channel", logic=Logic.HIGH
    )

    daq.read_digital_line("test_channel")

    mock_driver.read_digital_line.assert_called_once()


def test_read_digital_line_unconfigured_channel():
    """Test that reading from an unconfigured channel raises an error."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    with pytest.raises(KeyError, match="Digital input channel 'unconfigured_channel' is not configured") as exc_info:
        daq.read_digital_line("unconfigured_channel")

    print(f"\nRaised error: {exc_info.value}")

    mock_driver.read_digital_line.assert_not_called()


def test_write_analog_value_unconfigured_channel():
    """Test that writing to an unconfigured analog output channel raises an error."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    with pytest.raises(KeyError, match="Analog output channel 'unconfigured_channel' is not configured"):
        daq.write_analog_value("unconfigured_channel", 5.0)

    mock_driver.write_analog_value.assert_not_called()


def test_close_relay_unconfigured_channel():
    """Test that closing an unconfigured relay channel raises an error."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    with pytest.raises(KeyError, match="Relay channel 'unconfigured_relay' is not configured"):
        daq.close_relay("unconfigured_relay")

    mock_driver.close_relay.assert_not_called()


def test_open_relay_unconfigured_channel():
    """Test that opening an unconfigured relay channel raises an error."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    with pytest.raises(KeyError, match="Relay channel 'unconfigured_relay' is not configured"):
        daq.open_relay("unconfigured_relay")

    mock_driver.open_relay.assert_not_called()


def test_write_digital_port_configured_channel():
    """Test that writing to a configured port channel works without error."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    daq.configure_digital_port(
        direction=Direction.OUTPUT, physical_channel="port0", logic=Logic.HIGH, port_width=8, alias="test_port"
    )

    daq.write_digital_port("test_port", 0xFF)

    mock_driver.write_digital_port.assert_called_once()


def test_write_digital_port_value_exceeds_width():
    """Test that writing a value wider than the configured port raises ValueError."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    daq.configure_digital_port(
        direction=Direction.OUTPUT, physical_channel="port0", logic=Logic.HIGH, port_width=8, alias="test_port"
    )

    with pytest.raises(ValueError, match="does not fit the 8-bit port 'test_port'"):
        daq.write_digital_port("test_port", 0x100)

    with pytest.raises(ValueError, match="does not fit the 8-bit port 'test_port'"):
        daq.write_digital_port("test_port", -1)

    mock_driver.write_digital_port.assert_not_called()


def test_write_digital_port_unconfigured_channel():
    """Test that writing to an unconfigured port channel raises KeyError."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    with pytest.raises(KeyError, match="Digital output channel 'unconfigured_port' is not configured"):
        daq.write_digital_port("unconfigured_port", 0xFF)

    mock_driver.write_digital_port.assert_not_called()


def test_read_digital_port_configured_channel():
    """Test that reading from a configured port channel works without error."""
    mock_driver = _make_mock_driver()
    mock_driver.read_digital_port.return_value = 0xFF

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    daq.configure_digital_port(
        direction=Direction.INPUT, physical_channel="port0", logic=Logic.HIGH, port_width=8, alias="test_port"
    )

    daq.read_digital_port("test_port")

    mock_driver.read_digital_port.assert_called_once()


def test_read_digital_port_unconfigured_channel():
    """Test that reading from an unconfigured port channel raises KeyError."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.open()
    with pytest.raises(KeyError, match="Digital input channel 'unconfigured_port' is not configured"):
        daq.read_digital_port("unconfigured_port")

    mock_driver.read_digital_port.assert_not_called()


# ---------------------------------------------------------------------------
# duplicate-channel guard (new configure_* surface)
# ---------------------------------------------------------------------------


# Enum args are passed as plain strings so these also confirm the new methods accept string enum values.
_NEW_CONFIGURE_CALLS = [
    (
        "configure_voltage_input",
        lambda daq, alias: daq.configure_voltage_input("ai0", alias=alias, terminal_config="RSE"),
    ),
    ("configure_voltage_output", lambda daq, alias: daq.configure_voltage_output("ao0", alias=alias)),
    ("configure_current_input", lambda daq, alias: daq.configure_current_input("ai1", alias=alias)),
    ("configure_current_output", lambda daq, alias: daq.configure_current_output("ao1", alias=alias)),
    (
        "configure_thermocouple_input",
        lambda daq, alias: daq.configure_thermocouple_input(
            "ai2", "K", alias=alias, cjc_source="INTERNAL", unit="degC"
        ),
    ),
    (
        "configure_digital_input",
        lambda daq, alias: daq.configure_digital_input("port0/line0", alias=alias, logic="HIGH"),
    ),
    (
        "configure_digital_output",
        lambda daq, alias: daq.configure_digital_output("port0/line1", alias=alias, logic="LOW"),
    ),
]


@pytest.mark.parametrize("name,call", _NEW_CONFIGURE_CALLS, ids=[name for name, _ in _NEW_CONFIGURE_CALLS])
def test_configure_same_alias_twice_raises(name, call):
    """Every new configure_* method rejects reconfiguring an already-configured alias."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()

    call(daq, "dup")
    with pytest.raises(ValueError, match="channel 'dup' is already configured"):
        call(daq, "dup")


def test_duplicate_channel_error_names_existing_kind_and_physical_channel():
    """The duplicate error reports the existing channel's kind and physical channel."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()
    daq.configure_voltage_input("cDAQ1Mod1/ai0", alias="v_in")

    with pytest.raises(
        ValueError,
        match=r"channel 'v_in' is already configured \(voltage_input on cDAQ1Mod1/ai0\); remove it before reconfiguring.",
    ):
        daq.configure_voltage_input("cDAQ1Mod1/ai1", alias="v_in")


def test_duplicate_channel_guard_is_global_across_configure_methods():
    """An alias is unique across the whole driver: a different configure_* method can't reuse it."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()
    daq.configure_voltage_input("ai0", alias="shared")

    with pytest.raises(ValueError, match=r"channel 'shared' is already configured \(voltage_input on ai0\)"):
        daq.configure_current_input("ai1", alias="shared")


# ---------------------------------------------------------------------------
# thermocouple units
# ---------------------------------------------------------------------------


def test_configure_thermocouple_input_records_requested_unit():
    """A valid unit is recorded as a pint unit and cjc_temp stays in that unit, not converted."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()

    daq.configure_thermocouple_input("ai0", "K", alias="tc0", unit="degF", cjc_source="CONSTANT", cjc_temp=77.0)

    channel = daq.ai_channels["tc0"]
    assert channel.unit == parse_unit("degF")
    assert channel.cjc_temp == 77.0


def test_configure_thermocouple_input_rejects_unknown_unit():
    """An unparseable unit raises and no channel is recorded."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()

    with pytest.raises(ValueError, match="unit 'CELSIUS' is not a known unit"):
        daq.configure_thermocouple_input("ai0", "K", alias="tc0", unit="CELSIUS")
    assert not daq.ai_channels


def test_configure_while_running_raises():
    """A new configure_* method refuses to configure a channel while acquisition is running (nothing recorded)."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()
    daq.configure_ai_hw_sample_rate(sample_rate=1000)
    daq.start(background=False)

    with pytest.raises(RuntimeError, match=r"cannot configure channel 'v0' while 'ut' is running; call stop\(\)"):
        daq.configure_voltage_input("ai0", alias="v0")
    assert not daq.ai_channels

    daq.stop()


def test_configure_after_stop_succeeds():
    """stop() clears the running guard so channels can be configured again."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()
    daq.configure_ai_hw_sample_rate(sample_rate=1000)
    daq.start(background=False)
    daq.stop()

    daq.configure_voltage_input("ai0", alias="v0")

    assert "v0" in daq.ai_channels


# ---------------------------------------------------------------------------
# open() guard
# ---------------------------------------------------------------------------


_GUARDED_METHODS = [
    ("start", lambda daq: daq.start()),
    ("read_analog", lambda daq: daq.read_analog()),
    ("write_analog_value", lambda daq: daq.write_analog_value("ch", 1.0)),
    ("write_digital_line", lambda daq: daq.write_digital_line("ch", 1)),
    ("read_digital_line", lambda daq: daq.read_digital_line("ch")),
    ("write_digital_port", lambda daq: daq.write_digital_port("ch", 1)),
    ("read_digital_port", lambda daq: daq.read_digital_port("ch")),
    ("close_relay", lambda daq: daq.close_relay("ch")),
    ("open_relay", lambda daq: daq.open_relay("ch")),
    ("get_points_in_buffer", lambda daq: daq.get_points_in_buffer()),
    (
        "configure_analog_channel",
        lambda daq: daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0"),
    ),
    ("configure_ai_sample_rate", lambda daq: daq.configure_ai_sample_rate(sample_rate=100)),
    (
        "configure_digital_line",
        lambda daq: daq.configure_digital_line(
            direction=Direction.OUTPUT, physical_channel="port0/line0", logic=Logic.HIGH
        ),
    ),
    (
        "configure_digital_port",
        lambda daq: daq.configure_digital_port(
            direction=Direction.OUTPUT, physical_channel="port0", logic=Logic.HIGH, port_width=8
        ),
    ),
    ("configure_relay_channel", lambda daq: daq.configure_relay_channel(physical_channel="3101")),
]


@pytest.mark.parametrize("method_name,call", _GUARDED_METHODS, ids=[name for name, _ in _GUARDED_METHODS])
def test_method_before_open_raises_not_open(method_name, call):
    """Every device-touching method raises InstrumentNotOpenError when called before open()."""
    daq = InstroDAQ(name="Bench DAQ", driver=_make_mock_driver())

    with pytest.raises(InstrumentNotOpenError, match="Bench DAQ"):
        call(daq)


def test_not_open_error_message_names_the_instrument():
    """The not-open error names the instance so users know which DAQ they forgot to open."""
    daq = InstroDAQ(name="myDAQ", driver=_make_mock_driver())

    with pytest.raises(InstrumentNotOpenError, match="InstroDAQ 'myDAQ' is not open. Call open\\(\\) first."):
        daq.read_analog()


def test_method_after_open_does_not_raise_not_open():
    """Opening clears the guard; the same call no longer raises InstrumentNotOpenError."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()

    daq.get_points_in_buffer()  # would raise InstrumentNotOpenError before open()


def test_method_after_close_raises_not_open_again():
    """close() re-arms the guard; calling a device method afterwards raises again."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()
    daq.close()

    with pytest.raises(InstrumentNotOpenError, match="ut"):
        daq.read_analog()


def test_close_without_open_runs_full_teardown():
    """close() before open() does not raise and still runs every teardown step: driver close and publisher close."""
    pub = Mock(name="publisher")
    daq = InstroDAQ(name="ut", driver=_make_mock_driver(), publishers=[pub])

    daq.close()  # must not raise InstrumentNotOpenError

    daq._driver.close.assert_called_once()
    pub.close.assert_called_once()


def test_double_close_does_not_raise_and_always_closes_driver():
    """close() never gates the driver close on state: a second close() re-runs it (the driver owns idempotency)."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()

    daq.close()
    daq.close()  # must not raise InstrumentNotOpenError

    assert daq._driver.close.call_count == 2


def test_close_after_failed_open_propagates_original_error():
    """A failed open() must not poison the finally: close() runs teardown without masking the real error."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq._driver.open.side_effect = RuntimeError("device unreachable")

    with pytest.raises(RuntimeError, match="device unreachable"):
        try:
            daq.open()
        finally:
            daq.close()  # must not raise InstrumentNotOpenError and mask the open failure

    daq._driver.close.assert_called_once()


# ---------------------------------------------------------------------------
# HWTimestamper tests
# ---------------------------------------------------------------------------


def test_hw_timestamper_seed_returns_correct_count():
    """seed() returns exactly `length` timestamps."""
    _, timestamps = HWTimestamper.seed(t_wall=10_000, dt=100, length=5)
    assert len(timestamps) == 5


def test_hw_timestamper_seed_last_timestamp_equals_t_wall():
    """The last timestamp in the seed batch is anchored to wall-clock time."""
    _, timestamps = HWTimestamper.seed(t_wall=10_000, dt=100, length=5)
    assert timestamps[-1] == 10_000


def test_hw_timestamper_seed_spacing_is_dt():
    """All consecutive timestamps in the seed batch are spaced by exactly dt."""
    _, timestamps = HWTimestamper.seed(t_wall=10_000, dt=100, length=5)
    for i in range(len(timestamps) - 1):
        assert timestamps[i + 1] - timestamps[i] == 100


def test_hw_timestamper_seed_computes_correct_t0():
    """The first timestamp equals t_wall - dt * (length - 1)."""
    _, timestamps = HWTimestamper.seed(t_wall=10_000, dt=100, length=5)
    assert timestamps[0] == 10_000 - 100 * 4


def test_hw_timestamper_seed_single_sample():
    """A single-sample seed returns [t_wall]."""
    _, timestamps = HWTimestamper.seed(t_wall=5_000, dt=100, length=1)
    assert timestamps == [5_000]


def test_hw_timestamper_next_batch_returns_correct_count():
    """next_batch() returns exactly `length` timestamps."""
    stamper, _ = HWTimestamper.seed(t_wall=10_000, dt=100, length=3)
    timestamps = stamper.next_batch(dt=100, length=4)
    assert len(timestamps) == 4


def test_hw_timestamper_next_batch_starts_one_dt_after_seed():
    """The first timestamp of next_batch is exactly dt after the seed's last."""
    stamper, seed_ts = HWTimestamper.seed(t_wall=10_000, dt=100, length=5)
    batch2 = stamper.next_batch(dt=100, length=3)
    assert batch2[0] == seed_ts[-1] + 100


def test_hw_timestamper_next_batch_spacing_is_dt():
    """All consecutive timestamps in next_batch are spaced by exactly dt."""
    stamper, _ = HWTimestamper.seed(t_wall=10_000, dt=100, length=5)
    timestamps = stamper.next_batch(dt=100, length=5)
    for i in range(len(timestamps) - 1):
        assert timestamps[i + 1] - timestamps[i] == 100


def test_hw_timestamper_next_batch_single_sample():
    """A single-sample next_batch returns [last_timestamp + dt]."""
    stamper, _ = HWTimestamper.seed(t_wall=10_000, dt=100, length=3)
    timestamps = stamper.next_batch(dt=100, length=1)
    assert timestamps == [10_100]


def test_hw_timestamper_contiguity_across_three_batches():
    """Timestamps are contiguous with uniform dt spacing across batch boundaries."""
    dt = 1000
    stamper, all_ts = HWTimestamper.seed(t_wall=1_000_000, dt=dt, length=10)
    all_ts += stamper.next_batch(dt=dt, length=10)
    all_ts += stamper.next_batch(dt=dt, length=10)

    assert len(all_ts) == 30
    for i in range(len(all_ts) - 1):
        assert all_ts[i + 1] - all_ts[i] == dt


def test_hw_timestamper_contiguity_with_varying_batch_sizes():
    """Contiguity holds when batch sizes vary."""
    dt = 500
    stamper, all_ts = HWTimestamper.seed(t_wall=100_000, dt=dt, length=3)
    for size in [7, 1, 15, 2]:
        all_ts += stamper.next_batch(dt=dt, length=size)

    assert len(all_ts) == 3 + 7 + 1 + 15 + 2
    for i in range(len(all_ts) - 1):
        assert all_ts[i + 1] - all_ts[i] == dt


def test_hw_timestamper_many_batches_no_drift():
    """No accumulation error after 100 batches."""
    dt = 1_000_000
    batch_size = 10
    num_batches = 100
    stamper, all_ts = HWTimestamper.seed(t_wall=1_000_000_000, dt=dt, length=batch_size)
    for _ in range(num_batches):
        all_ts += stamper.next_batch(dt=dt, length=batch_size)

    total_samples = batch_size * (1 + num_batches)
    expected_last = all_ts[0] + (total_samples - 1) * dt
    assert all_ts[-1] == expected_last


def test_hw_timestamper_rapid_reads_no_overlap():
    """Regression: two reads returning 0.5ms apart still produce non-overlapping timestamps.

    This is the exact bug scenario from INSTRO-150. At 1kHz with 100 samples per batch,
    each batch covers 100ms of data. If a second read returns only 0.5ms after the first,
    HWTimestamper must still place batch2 entirely after batch1.
    """
    dt = 1_000_000  # 1kHz -> 1ms per sample in nanoseconds
    length = 100

    t_wall_1 = 100_000_000  # first read returns at 100ms
    stamper, batch1 = HWTimestamper.seed(t_wall=t_wall_1, dt=dt, length=length)

    # Second read returns only 0.5ms later — much faster than the batch duration
    batch2 = stamper.next_batch(dt=dt, length=length)

    assert batch2[0] > batch1[-1]
    assert batch2[0] == batch1[-1] + dt


def test_hw_timestamper_vs_old_algorithm_overlap_demonstration():
    """Demonstrate that the old backstamp algorithm overlaps while HWTimestamper does not.

    The old approach called create_timestamps_from_dt(t0=t_wall, dt, length, backstamp=True)
    independently per read, causing overlap when reads return in rapid succession.
    """
    dt = 1_000_000  # 1kHz
    length = 100
    t_wall_1 = 100_000_000
    t_wall_2 = 100_500_000  # 0.5ms later

    # Old algorithm (backstamp from each t_wall independently)
    old_batch1 = [t_wall_1 - dt * (length - 1) + i * dt for i in range(length)]
    old_batch2 = [t_wall_2 - dt * (length - 1) + i * dt for i in range(length)]
    assert old_batch2[0] < old_batch1[-1], "Old algorithm should produce overlap"

    # New algorithm (HWTimestamper)
    stamper, new_batch1 = HWTimestamper.seed(t_wall=t_wall_1, dt=dt, length=length)
    new_batch2 = stamper.next_batch(dt=dt, length=length)
    assert new_batch2[0] > new_batch1[-1], "HWTimestamper must not overlap"


def test_hw_timestamper_large_dt():
    """Correct behaviour with large dt (1 second in nanoseconds)."""
    dt = 1_000_000_000
    stamper, batch1 = HWTimestamper.seed(t_wall=5_000_000_000, dt=dt, length=5)
    batch2 = stamper.next_batch(dt=dt, length=5)

    all_ts = batch1 + batch2
    assert len(all_ts) == 10
    for i in range(len(all_ts) - 1):
        assert all_ts[i + 1] - all_ts[i] == dt


@pytest.mark.parametrize("rate", [1, 100, 1000, 10000, 51200])
def test_hw_timestamper_realistic_sample_rates(rate: int):
    """Contiguity holds for common DAQ sample rates."""
    dt = round(1e9 / rate)
    stamper, all_ts = HWTimestamper.seed(t_wall=1_000_000_000, dt=dt, length=50)
    all_ts += stamper.next_batch(dt=dt, length=50)
    all_ts += stamper.next_batch(dt=dt, length=50)

    assert len(all_ts) == 150
    for i in range(len(all_ts) - 1):
        assert all_ts[i + 1] - all_ts[i] == dt


def test_hw_timestamper_state_preserved_across_calls():
    """Internal _last_timestamp tracks the last emitted timestamp."""
    stamper, seed_ts = HWTimestamper.seed(t_wall=10_000, dt=100, length=5)
    assert stamper._last_timestamp == seed_ts[-1]

    batch2 = stamper.next_batch(dt=100, length=3)
    assert stamper._last_timestamp == batch2[-1]

    batch3 = stamper.next_batch(dt=100, length=7)
    assert stamper._last_timestamp == batch3[-1]


def test_hw_timestamper_seed_returns_tuple():
    """seed() returns a (HWTimestamper, list[int]) tuple."""
    result = HWTimestamper.seed(t_wall=10_000, dt=100, length=5)
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], HWTimestamper)
    assert isinstance(result[1], list)


def test_hw_timestamper_driver_usage_pattern():
    """Simulate the if-None-then-seed-else-next_batch pattern used in NI/LabJack drivers."""
    dt = 1_000_000  # 1kHz
    length = 10
    timestamper = None
    all_ts: list[int] = []

    # Simulate 5 read iterations with varying wall-clock return times
    wall_times = [100_000_000, 100_500_000, 250_000_000, 250_100_000, 400_000_000]

    for t_wall in wall_times:
        if timestamper is None:
            timestamper, timestamps = HWTimestamper.seed(t_wall=t_wall, dt=dt, length=length)
        else:
            timestamps = timestamper.next_batch(dt=dt, length=length)
        all_ts += timestamps

    # All 50 timestamps must be contiguous with uniform spacing
    assert len(all_ts) == 50
    for i in range(len(all_ts) - 1):
        assert all_ts[i + 1] - all_ts[i] == dt

    # Only the first batch should be anchored to t_wall; verify first batch's last == wall_times[0]
    assert all_ts[length - 1] == wall_times[0]


# --- legacy_naming ---


def _legacy_daq_with_digital_channel(direction: Direction):
    """Build an InstroDAQ(legacy_naming=True) with a single configured digital line channel."""
    mock_driver = _make_mock_driver()
    mock_driver.read_digital_line.return_value = 1
    mock_driver.read_digital_port.return_value = 5

    daq = InstroDAQ(name="ut", driver=mock_driver, legacy_naming=True)
    daq.open()
    daq.configure_digital_line(direction=direction, physical_channel="port0/line0", alias="di0", logic=Logic.HIGH)
    return daq


def test_legacy_naming_write_digital_line_publishes_bare_alias():
    """Legacy DAQ digital writes publish under the bare alias (no `{name}.` prefix, no `.cmd`)."""
    daq = _legacy_daq_with_digital_channel(Direction.OUTPUT)
    command = daq.write_digital_line("di0", 1)
    assert "di0" in command.channel_data
    assert "ut.di0" not in command.channel_data
    assert "ut.di0.cmd" not in command.channel_data


def test_legacy_naming_read_digital_line_publishes_bare_alias():
    """Legacy DAQ digital reads publish under the bare alias (no `{name}.` prefix)."""
    daq = _legacy_daq_with_digital_channel(Direction.INPUT)
    measurement = daq.read_digital_line("di0")
    assert "di0" in measurement.channel_data
    assert "ut.di0" not in measurement.channel_data


def test_default_naming_write_digital_line_publishes_with_prefix_and_cmd():
    """Default DAQ digital writes are prefixed and suffixed (v1.0 form)."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.open()
    daq.configure_digital_line(
        direction=Direction.OUTPUT, physical_channel="port0/line0", alias="do0", logic=Logic.HIGH
    )
    command = daq.write_digital_line("do0", 1)
    assert "ut.do0.cmd" in command.channel_data


def test_default_naming_write_digital_line_preserves_int_value_type():
    """DAQ digital writes publish the raw int value, not a float-coerced copy."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.open()
    daq.configure_digital_line(
        direction=Direction.OUTPUT, physical_channel="port0/line0", alias="do0", logic=Logic.HIGH
    )
    command = daq.write_digital_line("do0", 1)
    value = command.channel_data["ut.do0.cmd"]
    assert value == 1
    assert isinstance(value, int)
    assert not isinstance(value, bool)


def test_default_naming_write_digital_port_preserves_int_value_type():
    """DAQ digital port writes publish the raw int value (e.g. a byte pattern), not a float-coerced copy."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.open()
    daq.configure_digital_port(
        direction=Direction.OUTPUT, physical_channel="port0", alias="port0", logic=Logic.HIGH, port_width=8
    )
    command = daq.write_digital_port("port0", 0xAA)
    value = command.channel_data["ut.port0.cmd"]
    assert value == 0xAA
    assert isinstance(value, int)


# --- read-only / frozen-snapshot contract ---


def test_channel_mapping_is_read_only():
    """The channel-dict properties return read-only mappings; mutating them raises."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()
    daq.configure_digital_line(
        direction=Direction.OUTPUT, physical_channel="port0/line0", alias="do0", logic=Logic.HIGH
    )

    with pytest.raises(TypeError):
        daq.do_channels["do0"] = "x"  # type: ignore[index]
    with pytest.raises(AttributeError):
        daq.do_channels.clear()  # type: ignore[attr-defined]


def test_channel_objects_are_frozen():
    """Channels handed back through a snapshot are frozen; attribute writes raise."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()
    daq.configure_digital_line(
        direction=Direction.OUTPUT, physical_channel="port0/line0", alias="do0", logic=Logic.HIGH
    )

    channel = daq.do_channels["do0"]
    with pytest.raises(FrozenInstanceError):
        channel.alias = "renamed"  # type: ignore[misc]


def test_channel_snapshot_is_not_a_live_view():
    """A captured snapshot does not reflect channels configured afterwards."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()
    daq.configure_digital_line(direction=Direction.OUTPUT, physical_channel="port0/line0", alias="a", logic=Logic.HIGH)

    snapshot = daq.do_channels
    daq.configure_digital_line(direction=Direction.OUTPUT, physical_channel="port0/line1", alias="b", logic=Logic.HIGH)

    assert "b" not in snapshot
    assert set(snapshot) == {"a"}
    assert set(daq.do_channels) == {"a", "b"}


def test_channels_property_returns_immutable_tuple():
    """The aggregate ``channels`` property returns a tuple snapshot."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()
    daq.configure_digital_line(
        direction=Direction.OUTPUT, physical_channel="port0/line0", alias="do0", logic=Logic.HIGH
    )

    assert isinstance(daq.channels, tuple)
    assert {ch.alias for ch in daq.channels} == {"do0"}


def test_stop_with_channel_type_forwards_kwarg_once():
    """stop(channel_type=...) must not pass channel_type both explicitly and via **kwargs."""
    mock_driver = _make_mock_driver()
    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.open()

    daq.stop(channel_type="analog_input")

    mock_driver.stop.assert_called_once_with(channel_type="analog_input")


def test_configure_ai_sample_rate_below_10hz_floors_samples_per_channel():
    """The samples_per_channel default must never be 0; sub-10 Hz rates floor at 1."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()

    daq.configure_ai_sample_rate(sample_rate=1.0)

    assert daq.ai_hw_timing_config is not None
    assert daq.ai_hw_timing_config.samples_per_channel == 1


# --- start(background=...) and read_analog dispatch ---


def _hw_timed_daq(name: str = "ut") -> tuple[InstroDAQ, _RecordingDriver]:
    """An InstroDAQ with one AI channel and a hardware sample rate configured."""
    mock_driver = _make_mock_driver()
    mock_driver._read_to_measurements.return_value = []
    daq = InstroDAQ(name=name, driver=mock_driver)
    daq.open()
    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0", alias="ai0")
    daq.configure_ai_hw_sample_rate(sample_rate=100, samples_per_channel=10)
    return daq, mock_driver


def _sw_timed_daq(name: str = "ut") -> tuple[InstroDAQ, _RecordingDriver]:
    """An InstroDAQ with one AI channel and a software-timed poll rate configured."""
    mock_driver = _make_mock_driver()
    mock_driver._read_to_measurements.return_value = []
    daq = InstroDAQ(name=name, driver=mock_driver)
    daq.open()
    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0", alias="ai0")
    daq.configure_ai_sw_sample_rate(sample_rate=100)
    return daq, mock_driver


def _untimed_daq(name: str = "ut") -> tuple[InstroDAQ, _RecordingDriver]:
    """An InstroDAQ with one AI channel and no timing mode configured."""
    mock_driver = _make_mock_driver()
    mock_driver._read_to_measurements.return_value = []
    daq = InstroDAQ(name=name, driver=mock_driver)
    daq.open()
    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0", alias="ai0")
    return daq, mock_driver


def test_hw_timed_start_with_background():
    """HW timing + background=True: the device acquires and the daemon fetches its buffer."""
    daq, mock_driver = _hw_timed_daq()

    daq.start()
    try:
        daq.get_channel("loop_time", wait_for_new_samples=True, timeout=5)

        assert daq._background_thread and daq._background_thread.is_alive()
        mock_driver.start.assert_called_once()
        mock_driver.fetch_analog.assert_called()
    finally:
        daq.stop()


def test_hw_timed_start_without_background():
    """HW timing + background=False: the device acquires and the caller fetches the buffer itself."""
    daq, mock_driver = _hw_timed_daq()

    daq.start(background=False)
    daq.read_analog()

    assert daq._background_thread is None
    mock_driver.start.assert_called_once()
    mock_driver.fetch_analog.assert_called_once()


def test_sw_timed_start_with_background():
    """SW timing + background=True: the daemon paces read_analog() and the device is never started."""
    daq, mock_driver = _sw_timed_daq()

    daq.start()
    try:
        daq.get_channel("loop_time", wait_for_new_samples=True, timeout=5)

        assert daq._background_thread and daq._background_thread.is_alive()
        mock_driver.read_analog.assert_called()
        mock_driver.start.assert_not_called()
    finally:
        daq.stop()


def test_sw_timed_start_without_background(caplog):
    """SW timing + background=False: nothing would pace the reads, so start() logs an error and starts nothing."""
    daq, mock_driver = _sw_timed_daq()

    with caplog.at_level(logging.ERROR):
        daq.start(background=False)

    assert "with SW AI timing configured is a no-op" in caplog.text
    assert daq._background_thread is None
    mock_driver.start.assert_not_called()


def test_untimed_start_with_background():
    """No timing + background=True: start() falls back to a SW-timed daemon at the default rate."""
    daq, mock_driver = _untimed_daq()

    daq.start()
    try:
        assert daq.is_sw_timing_configured
        assert daq.background_interval == pytest.approx(1 / InstroDAQ.DEFAULT_SW_SAMPLE_RATE)
        assert daq._background_thread and daq._background_thread.is_alive()
        mock_driver.start.assert_not_called()
    finally:
        daq.stop()


def test_untimed_start_without_background(caplog):
    """No timing + background=False: no clock and no daemon, so start() logs an error and starts nothing."""
    daq, mock_driver = _untimed_daq()

    with caplog.at_level(logging.ERROR):
        daq.start(background=False)

    assert "without AI timing configured is unnecessary" in caplog.text
    assert not daq.is_sw_timing_configured
    assert daq._background_thread is None
    mock_driver.start.assert_not_called()


@pytest.mark.parametrize("timing", ["software", "hardware"], ids=["software-timed", "hardware-timed"])
@pytest.mark.parametrize("measurement_count", [0, 1, 2])
def test_read_analog_preserves_public_return_shape(timing: str, measurement_count: int):
    """Internal helpers return lists while public reads unwrap one Measurement."""
    if timing == "hardware":
        daq, mock_driver = _hw_timed_daq()
        internal_read = daq._fetch_analog_hw_timed
    else:
        mock_driver = _make_mock_driver()
        daq = InstroDAQ(name="ut", driver=mock_driver)
        daq.open()
        daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0", alias="ai0")
        internal_read = daq._software_timed_read

    measurements = [
        Measurement(channel_data={"ut.ai0": [float(index)]}, timestamps=[index]) for index in range(measurement_count)
    ]
    mock_driver._read_to_measurements.return_value = measurements

    internal_result = internal_read()
    public_result = daq.read_analog()

    assert internal_result == measurements
    assert public_result == (measurements[0] if measurement_count == 1 else measurements)


def test_restart_registers_background_fetch_exactly_once():
    """start() after stop() must not register a second _fetch_analog_hw_timed daemon function."""
    daq, _ = _hw_timed_daq()
    try:
        daq.start()
        daq.stop()
        daq.start()

        fetchers = [method for method, _, _ in daq._background_methods if method == daq._fetch_analog_hw_timed]
        assert len(fetchers) == 1
    finally:
        daq.stop()


def test_read_analog_raises_while_daemon_running():
    """The daemon owns the buffer, so a manual read_analog() alongside it raises."""
    daq, _ = _hw_timed_daq()

    daq.start()
    try:
        with pytest.raises(RuntimeError, match="background acquisition daemon is running"):
            daq.read_analog()
    finally:
        daq.stop()


# --- software-timed background daemon ---


def test_configure_ai_sw_sample_rate_sets_loop_period_and_buffer_length():
    """SW timing paces the daemon at 1/rate and sizes the channel buffer for ~10 s of samples."""
    daq, _ = _sw_timed_daq()

    assert daq.background_interval == pytest.approx(0.01)
    assert daq._channel_buffer_length == 1000
    assert daq.is_sw_timing_configured
    assert not daq.is_hw_timing_configured


@pytest.mark.parametrize("order", ["hw-then-sw", "sw-then-hw"])
def test_hw_and_sw_timing_are_mutually_exclusive(order: str):
    """A DAQ is either hardware- or software-timed; configuring the second mode raises."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()

    if order == "hw-then-sw":
        daq.configure_ai_hw_sample_rate(sample_rate=100)
        with pytest.raises(TimingConfigException, match="already configured for hardware timing"):
            daq.configure_ai_sw_sample_rate(sample_rate=10)
    else:
        daq.configure_ai_sw_sample_rate(sample_rate=10)
        with pytest.raises(TimingConfigException, match="already configured for software timing"):
            daq.configure_ai_hw_sample_rate(sample_rate=100)


def test_configure_ai_sw_sample_rate_rejects_nonpositive_rate():
    """A 0 Hz poll rate has no period; the guard raises before it can poison the loop interval."""
    daq = InstroDAQ(name="ut", driver=_make_mock_driver())
    daq.open()

    with pytest.raises(ValueError, match="greater than 0 Hz"):
        daq.configure_ai_sw_sample_rate(sample_rate=0)
    assert not daq.is_sw_timing_configured


def test_sw_timed_stop_leaves_the_device_alone():
    """SW-timed acquisition never started the device, so stop() must not issue a device stop."""
    daq, mock_driver = _sw_timed_daq()
    daq.start()

    daq.stop()

    assert daq._background_thread is not None
    assert not daq._background_thread.is_alive()
    mock_driver.stop.assert_not_called()


def test_hw_and_sw_timed_daqs_run_in_parallel():
    """One HW-timed and one SW-timed DAQ run side by side, each on its own driver, buffer, and pacing."""
    hw_daq, hw_driver = _hw_timed_daq(name="hw")
    sw_daq, sw_driver = _sw_timed_daq(name="sw")
    hw_driver._read_to_measurements.return_value = [Measurement(channel_data={"hw.ai0": [1.0]}, timestamps=[1])]
    sw_driver._read_to_measurements.return_value = [Measurement(channel_data={"sw.ai0": [2.0]}, timestamps=[2])]

    hw_daq.start()
    sw_daq.start()
    try:
        assert hw_daq.get_channel("ai0", wait_for_new_samples=True, timeout=5).latest == 1.0
        assert sw_daq.get_channel("ai0", wait_for_new_samples=True, timeout=5).latest == 2.0

        # Neither daemon reaches the other's driver, and each uses only its own timing path.
        hw_driver.fetch_analog.assert_called()
        hw_driver.read_analog.assert_not_called()
        sw_driver.read_analog.assert_called()
        sw_driver.fetch_analog.assert_not_called()

        # Independent pacing: the HW loop free-runs on the blocking fetch, the SW loop on its interval.
        assert hw_daq.background_interval == 0
        assert sw_daq.background_interval == pytest.approx(0.01)
    finally:
        hw_daq.stop()
        sw_daq.stop()

    hw_driver.stop.assert_called_once()
    sw_driver.stop.assert_not_called()
