"""Unit tests for DAQ driver functionality."""

from unittest.mock import Mock

import pytest

from instro.daq import InstroDAQ
from instro.daq.drivers import HWTimestamper
from instro.daq.types import DigitalPortWidth, Direction, Logic


def test_write_digital_line_configured_channel():
    """Test that writing to a configured channel works without error."""
    # Arrange: Create a mock driver with proper return values
    mock_driver = Mock()

    # Mock the channel object that define_digital_channel should return
    mock_channel = Mock()
    mock_channel.alias = "test_channel"
    mock_driver.define_digital_channel.return_value = mock_channel

    # Create DAQ instance
    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    # Configure a digital output channel
    daq.configure_digital_channel(
        direction=Direction.OUTPUT, physical_channel="port0/line0", logic=Logic.HIGH, alias="test_channel"
    )

    # Act: Write to the channel
    daq.write_digital_line("test_channel", 1)

    # Assert: Verify write was called
    mock_driver.write_digital_line.assert_called_once()


def test_write_digital_line_unconfigured_channel():
    """Test that writing to an unconfigured channel raises an error."""
    # Arrange: Create a mock driver with proper return values
    mock_driver = Mock()

    # Mock the channel object that define_digital_channel should return
    mock_channel = Mock()
    mock_channel.alias = "test_channel"
    mock_driver.define_digital_channel.return_value = mock_channel

    # Create DAQ instance
    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    # Do not configure a digital output channel

    # Act: Write to an unconfigured channel
    with pytest.raises(KeyError, match="Digital output channel 'unconfigured_channel' is not configured") as exc_info:
        daq.write_digital_line("unconfigured_channel", 1)

    print(f"\nRaised error: {exc_info.value}")

    # Assert: Verify write was not called
    mock_driver.write_digital_line.assert_not_called()


def test_read_digital_line_configured_channel():
    """Test that reading from a configured channel works without error."""
    # Arrange: Create a mock driver with proper return values
    mock_driver = Mock()

    # Mock the channel object that define_digital_channel should return
    mock_channel = Mock()
    mock_channel.alias = "test_channel"
    mock_driver.define_digital_channel.return_value = mock_channel

    # Mock the read_digital_line to return an actual number (otherwise it returns a Mock object)
    mock_driver.read_digital_line.return_value = 1

    # Create DAQ instance
    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    # Configure a digital input channel
    daq.configure_digital_channel(
        direction=Direction.INPUT, physical_channel="port0/line0", alias="test_channel", logic=Logic.HIGH
    )

    # Act: Write to the channel
    daq.read_digital_line("test_channel")

    # Assert: Verify write was called
    mock_driver.read_digital_line.assert_called_once()


def test_read_digital_line_unconfigured_channel():
    """Test that reading from an unconfigured channel raises an error."""
    # Arrange: Create a mock driver with proper return values
    mock_driver = Mock()

    # Mock the channel object that define_digital_channel should return
    mock_channel = Mock()
    mock_channel.alias = "test_channel"
    mock_driver.define_digital_channel.return_value = mock_channel

    # Create DAQ instance
    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    # Do not configure a digital output channel

    # Act: Write to an unconfigured channel
    with pytest.raises(KeyError, match="Digital input channel 'unconfigured_channel' is not configured") as exc_info:
        daq.read_digital_line("unconfigured_channel")

    print(f"\nRaised error: {exc_info.value}")

    # Assert: Verify read was not called
    mock_driver.read_digital_line.assert_not_called()


def test_write_analog_value_unconfigured_channel():
    """Test that writing to an unconfigured analog output channel raises an error."""
    mock_driver = Mock()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    with pytest.raises(KeyError, match="Analog output channel 'unconfigured_channel' is not configured"):
        daq.write_analog_value("unconfigured_channel", 5.0)

    mock_driver.write_analog_value.assert_not_called()


def test_close_relay_unconfigured_channel():
    """Test that closing an unconfigured relay channel raises an error."""
    mock_driver = Mock()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    with pytest.raises(KeyError, match="Relay channel 'unconfigured_relay' is not configured"):
        daq.close_relay("unconfigured_relay")

    mock_driver.close_relay.assert_not_called()


def test_open_relay_unconfigured_channel():
    """Test that opening an unconfigured relay channel raises an error."""
    mock_driver = Mock()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    with pytest.raises(KeyError, match="Relay channel 'unconfigured_relay' is not configured"):
        daq.open_relay("unconfigured_relay")

    mock_driver.open_relay.assert_not_called()


def test_write_digital_port_configured_channel():
    """Test that writing to a configured port channel works without error."""
    mock_driver = Mock()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.configure_digital_channel(
        direction=Direction.OUTPUT,
        physical_channel="port0",
        logic=Logic.HIGH,
        alias="test_port",
        port_width=DigitalPortWidth.WIDTH_8,
    )

    daq.write_digital_port("test_port", 0xFF)

    mock_driver.write_digital_port.assert_called_once()


def test_write_digital_port_unconfigured_channel():
    """Test that writing to an unconfigured port channel raises KeyError."""
    mock_driver = Mock()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    with pytest.raises(KeyError, match="Digital output channel 'unconfigured_port' is not configured"):
        daq.write_digital_port("unconfigured_port", 0xFF)

    mock_driver.write_digital_port.assert_not_called()


def test_read_digital_port_configured_channel():
    """Test that reading from a configured port channel works without error."""
    mock_driver = Mock()
    mock_driver.read_digital_port.return_value = 0xFF

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    daq.configure_digital_channel(
        direction=Direction.INPUT,
        physical_channel="port0",
        logic=Logic.HIGH,
        alias="test_port",
        port_width=DigitalPortWidth.WIDTH_8,
    )

    daq.read_digital_port("test_port")

    mock_driver.read_digital_port.assert_called_once()


def test_read_digital_port_unconfigured_channel():
    """Test that reading from an unconfigured port channel raises KeyError."""
    mock_driver = Mock()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

    with pytest.raises(KeyError, match="Digital input channel 'unconfigured_port' is not configured"):
        daq.read_digital_port("unconfigured_port")

    mock_driver.read_digital_port.assert_not_called()


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

    This is the exact bug scenario from CON-1531. At 1kHz with 100 samples per batch,
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
    """Build an InstroDAQ(legacy_naming=True) with a single configured digital channel."""
    mock_driver = Mock()
    mock_channel = Mock()
    mock_channel.alias = "di0"
    mock_driver.define_digital_channel.return_value = mock_channel
    mock_driver.read_digital_line.return_value = 1
    mock_driver.read_digital_port.return_value = 5

    daq = InstroDAQ(name="ut", driver=mock_driver, legacy_naming=True)
    daq.configure_digital_channel(direction=direction, physical_channel="port0/line0", alias="di0", logic=Logic.HIGH)
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
    mock_driver = Mock()
    mock_channel = Mock()
    mock_channel.alias = "do0"
    mock_driver.define_digital_channel.return_value = mock_channel

    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.configure_digital_channel(
        direction=Direction.OUTPUT, physical_channel="port0/line0", alias="do0", logic=Logic.HIGH
    )
    command = daq.write_digital_line("do0", 1)
    assert "ut.do0.cmd" in command.channel_data


def test_default_naming_write_digital_line_preserves_int_value_type():
    """DAQ digital writes publish the raw int value, not a float-coerced copy."""
    mock_driver = Mock()
    mock_channel = Mock()
    mock_channel.alias = "do0"
    mock_driver.define_digital_channel.return_value = mock_channel

    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.configure_digital_channel(
        direction=Direction.OUTPUT, physical_channel="port0/line0", alias="do0", logic=Logic.HIGH
    )
    command = daq.write_digital_line("do0", 1)
    value = command.channel_data["ut.do0.cmd"]
    assert value == 1
    assert isinstance(value, int)
    assert not isinstance(value, bool)


def test_default_naming_write_digital_port_preserves_int_value_type():
    """DAQ digital port writes publish the raw int value (e.g. a byte pattern), not a float-coerced copy."""
    mock_driver = Mock()

    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.configure_digital_channel(
        direction=Direction.OUTPUT,
        physical_channel="port0",
        alias="port0",
        logic=Logic.HIGH,
        port_width=DigitalPortWidth.WIDTH_8,
    )
    command = daq.write_digital_port("port0", 0xAA)
    value = command.channel_data["ut.port0.cmd"]
    assert value == 0xAA
    assert isinstance(value, int)


# ---------------------------------------------------------------------------
# DAQTask / DAQSamples / default-task / multi-task semantics
# ---------------------------------------------------------------------------


def test_daqtask_defaults():
    from instro.daq.types import DAQTask

    task = DAQTask(name="t")
    assert task.name == "t"
    assert task.channels == []
    assert task.timing_config is None


def test_daqsamples_shape():
    from instro.daq.types import DAQSamples

    s = DAQSamples(channel_data={"ai0": [1.0, 2.0]}, timestamps_ns=[100, 200])
    assert s.channel_data == {"ai0": [1.0, 2.0]}
    assert s.timestamps_ns == [100, 200]


def test_default_task_created_on_first_configure():
    """The default task is lazily registered on the first configure call."""
    mock_driver = Mock()
    daq = InstroDAQ(name="ut", driver=mock_driver)

    # No tasks before any configure call
    assert daq.tasks == {}

    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0")

    assert "default" in daq.tasks
    default = daq.tasks["default"]
    assert [ch.alias for ch in default.channels] == ["ai0"]
    # Driver received the channel scoped to the default task
    mock_driver.register_task.assert_called_once()
    mock_driver.configure_ai_channel.assert_called_once()
    task_arg, channel_arg = mock_driver.configure_ai_channel.call_args.args
    assert task_arg is default
    assert channel_arg.alias == "ai0"


def test_default_task_holds_mixed_kinds():
    """A single default task can hold both analog and digital channels (unified-scan model)."""
    mock_driver = Mock()
    daq = InstroDAQ(name="ut", driver=mock_driver)

    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0")
    daq.configure_digital_channel(direction=Direction.INPUT, physical_channel="di0", logic=Logic.HIGH)

    assert list(daq.tasks.keys()) == ["default"]
    aliases = [ch.alias for ch in daq.tasks["default"].channels]
    assert aliases == ["ai0", "di0"]
    # Only one register_task call (the default), even with mixed kinds
    mock_driver.register_task.assert_called_once()


def test_configure_ai_sample_rate_sets_timing_on_default_task():
    mock_driver = Mock()
    daq = InstroDAQ(name="ut", driver=mock_driver)

    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0")
    daq.configure_ai_sample_rate(1000.0)

    default = daq.tasks["default"]
    assert default.timing_config is not None
    assert default.timing_config.sample_rate == 1000.0
    mock_driver.configure_timing.assert_called_once_with(default)


def test_create_task_with_explicit_timing():
    mock_driver = Mock()
    daq = InstroDAQ(name="ut", driver=mock_driver)

    fast = daq.create_task("fast", sample_rate=10_000)

    assert fast.name == "fast"
    assert fast.timing_config is not None
    assert fast.timing_config.sample_rate == 10_000
    assert daq.tasks["fast"] is fast
    mock_driver.register_task.assert_called_once_with(fast)
    mock_driver.configure_timing.assert_called_once_with(fast)


def test_create_task_rejects_duplicate_names():
    mock_driver = Mock()
    daq = InstroDAQ(name="ut", driver=mock_driver)

    daq.create_task("x")
    with pytest.raises(ValueError, match="Task 'x' already exists"):
        daq.create_task("x")


def test_multi_task_independent_for_drivers_that_allow_it():
    """A driver that doesn't restrict tasks accepts multiple."""
    mock_driver = Mock()
    daq = InstroDAQ(name="ut", driver=mock_driver)

    fast = daq.create_task("fast", sample_rate=10_000)
    slow = daq.create_task("slow", sample_rate=100)

    assert set(daq.tasks.keys()) == {"fast", "slow"}
    assert fast.timing_config is not None and slow.timing_config is not None
    assert fast.timing_config.sample_rate == 10_000
    assert slow.timing_config.sample_rate == 100


def test_unregistered_task_object_rejected():
    """Passing a DAQTask object that isn't registered on this DAQ raises."""
    from instro.daq.types import DAQTask

    mock_driver = Mock()
    daq = InstroDAQ(name="ut", driver=mock_driver)

    foreign = DAQTask(name="elsewhere")
    with pytest.raises(ValueError, match="not registered with this DAQ"):
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel="ai0",
            task=foreign,
        )


def test_unknown_task_name_rejected():
    """Passing a task name that doesn't exist raises a clear error."""
    mock_driver = Mock()
    daq = InstroDAQ(name="ut", driver=mock_driver)

    with pytest.raises(ValueError, match="Task 'missing' is not configured"):
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel="ai0",
            task="missing",
        )


def _running_state_mocks() -> tuple[Mock, set]:
    """Mock driver whose is_running reflects state mutated by start_task/stop_task."""
    mock_driver = Mock()
    running: set[str] = set()
    mock_driver.is_running.side_effect = lambda t: t.name in running
    mock_driver.start_task.side_effect = lambda t: running.add(t.name)
    mock_driver.stop_task.side_effect = lambda t: running.discard(t.name)
    return mock_driver, running


def test_targeted_stop_keeps_worker_alive_when_other_tasks_running():
    """stop(task=...) leaves the worker thread up as long as another task is running."""
    mock_driver, running = _running_state_mocks()
    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.background_enable = False

    fast = daq.create_task("fast", sample_rate=1000)
    slow = daq.create_task("slow", sample_rate=100)

    daq.start()
    assert running == {"fast", "slow"}
    assert daq._background_thread is not None and daq._background_thread.is_alive()

    # Stop only fast — slow keeps going and the worker must stay alive.
    daq.stop(task=fast)
    assert running == {"slow"}
    assert daq._background_thread.is_alive()

    # Now stop slow — worker comes down because nothing is running.
    daq.stop(task=slow)
    assert running == set()
    assert not daq._background_thread.is_alive()


def test_full_stop_tears_down_everything():
    """stop() (no task arg) stops the worker thread and every running task."""
    mock_driver, running = _running_state_mocks()
    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.background_enable = False

    fast = daq.create_task("fast", sample_rate=1000)
    slow = daq.create_task("slow", sample_rate=100)

    daq.start()
    daq.stop()

    assert running == set()
    assert not daq._background_thread.is_alive()
    stopped = [c.args[0] for c in mock_driver.stop_task.call_args_list]
    assert fast in stopped and slow in stopped


def test_targeted_start_brings_up_worker_thread():
    """start(task=...) ensures the worker is running, same as start()."""
    mock_driver, _ = _running_state_mocks()
    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.background_enable = False

    fast = daq.create_task("fast", sample_rate=10_000)

    # Targeted start: only fast should be started, but the worker thread comes up.
    daq.start(task=fast)
    started_args = [c.args[0] for c in mock_driver.start_task.call_args_list]
    assert started_args == [fast]
    assert daq._background_thread is not None and daq._background_thread.is_alive()

    # Cleanup so the test doesn't leak a live thread.
    daq.stop()


def test_daemon_registered_once_across_multiple_starts():
    """Repeated start() calls must not duplicate the background fetch daemon."""
    mock_driver, _ = _running_state_mocks()
    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.background_enable = False

    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0")
    daq.configure_ai_sample_rate(1000)

    daq.start()
    daq.stop()
    daq.start()

    # Exactly one entry in the background daemon function list.
    assert len(daq._background_methods) == 1

    daq.stop()


def test_daemon_registered_for_every_hw_timed_task():
    """Every HW-timed task with channels gets its own background fetch daemon."""
    mock_driver, _ = _running_state_mocks()
    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.background_enable = False

    fast = daq.create_task("fast", sample_rate=1000)
    slow = daq.create_task("slow", sample_rate=100)
    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0", task=fast)
    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai1", task=slow)

    daq.start()

    # One daemon per HW-timed task with channels.
    assert len(daq._background_methods) == 2
    assert daq._daemons_registered == {"fast", "slow"}

    daq.stop()


def test_daemon_registered_for_tasks_created_after_first_start():
    """Tasks created after an initial start() also get a daemon on the next start()."""
    mock_driver, _ = _running_state_mocks()
    daq = InstroDAQ(name="ut", driver=mock_driver)
    daq.background_enable = False

    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai0")
    daq.configure_ai_sample_rate(1000)
    daq.start()
    assert len(daq._background_methods) == 1
    daq.stop()

    # Add a second task after the first lifecycle has already brought up the daemon.
    extra = daq.create_task("extra", sample_rate=500)
    daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="ai1", task=extra)
    daq.start()

    assert len(daq._background_methods) == 2
    assert daq._daemons_registered == {"default", "extra"}

    daq.stop()


def test_driver_exposed_publicly():
    """The driver is accessible via the public `driver` attribute for vendor escape-hatch use."""
    mock_driver = Mock()
    daq = InstroDAQ(name="ut", driver=mock_driver)
    assert daq.driver is mock_driver


# ---------------------------------------------------------------------------
# Import-graph guard: vendor drivers must not depend on InstroDAQ.
#
# Rationale: drivers receive all context they need via DAQTask arguments. Any
# reach-back into the facade re-introduces the HAL pattern this framework was
# refactored to remove. The check is structural and runs as a unit test so it
# fires in CI without requiring vendor hardware to be present.
# ---------------------------------------------------------------------------

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DRIVER_PATHS = [
    _REPO_ROOT / "instro" / "daq" / "drivers" / "keysight_34980a.py",
    _REPO_ROOT / "packages" / "instro-daq-ni" / "instro" / "daq" / "drivers" / "ni" / "nidaq.py",
    _REPO_ROOT / "packages" / "instro-daq-mcc" / "instro" / "daq" / "drivers" / "mcc" / "mccdaq.py",
    _REPO_ROOT / "packages" / "instro-daq-labjack" / "instro" / "daq" / "drivers" / "labjack" / "t_series.py",
]


def _imports_instrodaq(path: Path) -> str | None:
    """Return a diagnostic message if `path` imports InstroDAQ, else None."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "InstroDAQ":
                    return f"{path} imports InstroDAQ via 'from {node.module} import InstroDAQ'"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "InstroDAQ" or alias.name.endswith(".InstroDAQ"):
                    return f"{path} imports {alias.name}"
    return None


@pytest.mark.parametrize(
    "path",
    [p for p in _DRIVER_PATHS if p.exists()],
    ids=lambda p: p.relative_to(_REPO_ROOT).as_posix(),
)
def test_driver_modules_dont_import_instrodaq(path: Path):
    """Vendor driver modules must not import InstroDAQ — context flows via DAQTask args."""
    diagnostic = _imports_instrodaq(path)
    assert diagnostic is None, diagnostic
