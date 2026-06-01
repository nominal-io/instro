"""Unit tests for DAQ driver functionality."""

from unittest.mock import Mock

import pytest

from instro.daq import InstroDAQ
from instro.daq.drivers import HWTimestamper
from instro.daq.types import Direction, Logic


def _make_mock_driver() -> Mock:
    """Mock driver with state dicts pre-initialized and ``configure_*`` side-effects that populate them.

    ``InstroDAQ.ai_channels`` etc. are ``@property`` proxies into the driver — so tests need a driver
    whose dicts behave like real dicts, and whose ``configure_*`` methods actually record the channel
    on the right dict (matching real-driver contract).
    """
    driver = Mock()
    driver.ai_channels = {}
    driver.ao_channels = {}
    driver.di_channels = {}
    driver.do_channels = {}
    driver.relay_channels = {}
    driver.ai_hw_timing_config = None
    driver.ao_hw_timing_config = None
    driver.di_hw_timing_config = None
    driver.do_hw_timing_config = None

    driver.configure_ai_channel.side_effect = lambda ch: driver.ai_channels.update({ch.alias: ch})
    driver.configure_ao_channel.side_effect = lambda ch: driver.ao_channels.update({ch.alias: ch})

    def _record_di_line(physical_channel, logic, logic_level=None, alias=None):
        key = alias or physical_channel
        driver.di_channels[key] = Mock(alias=key, physical_channel=physical_channel, logic=logic)

    def _record_do_line(physical_channel, logic, logic_level=None, alias=None):
        key = alias or physical_channel
        driver.do_channels[key] = Mock(alias=key, physical_channel=physical_channel, logic=logic)

    def _record_di_port(physical_channel, logic, port_width, logic_level=None, alias=None):
        key = alias or physical_channel
        driver.di_channels[key] = Mock(alias=key, physical_channel=physical_channel, logic=logic, width=port_width)

    def _record_do_port(physical_channel, logic, port_width, logic_level=None, alias=None):
        key = alias or physical_channel
        driver.do_channels[key] = Mock(alias=key, physical_channel=physical_channel, logic=logic, width=port_width)

    driver.configure_di_line_channel.side_effect = _record_di_line
    driver.configure_do_line_channel.side_effect = _record_do_line
    driver.configure_di_port_channel.side_effect = _record_di_port
    driver.configure_do_port_channel.side_effect = _record_do_port
    return driver


def test_write_digital_line_configured_channel():
    """Test that writing to a configured channel works without error."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

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

    daq.configure_digital_port(
        direction=Direction.OUTPUT, physical_channel="port0", logic=Logic.HIGH, port_width=8, alias="test_port"
    )

    daq.write_digital_port("test_port", 0xFF)

    mock_driver.write_digital_port.assert_called_once()


def test_write_digital_port_unconfigured_channel():
    """Test that writing to an unconfigured port channel raises KeyError."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(
        name="Test DAQ",
        driver=mock_driver,
    )

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
    daq.configure_digital_line(
        direction=Direction.OUTPUT, physical_channel="port0/line0", alias="do0", logic=Logic.HIGH
    )
    command = daq.write_digital_line("do0", 1)
    assert "ut.do0.cmd" in command.channel_data


def test_default_naming_write_digital_line_preserves_int_value_type():
    """DAQ digital writes publish the raw int value, not a float-coerced copy."""
    mock_driver = _make_mock_driver()

    daq = InstroDAQ(name="ut", driver=mock_driver)
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
    daq.configure_digital_port(
        direction=Direction.OUTPUT, physical_channel="port0", alias="port0", logic=Logic.HIGH, port_width=8
    )
    command = daq.write_digital_port("port0", 0xAA)
    value = command.channel_data["ut.port0.cmd"]
    assert value == 0xAA
    assert isinstance(value, int)
