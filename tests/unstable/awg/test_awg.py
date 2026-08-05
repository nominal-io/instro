"""Tests for AWGDriverBase contract and InstroAWG's generic wiring to a driver.

Scoped to what's actually generic about the abstraction layer: the driver contract, lifecycle,
the shared check_errors/tagging conventions, and channel-bounds enforcement across every method.
Per-method roundtrip/packaging behavior (set_waveform, amplitude, offset, output, modulation, ...)
is the responsibility of each vendor driver's own software test file, not re-derived here.
"""

from unittest.mock import MagicMock

import pytest

from instro.unstable.awg.awg import AWGDriverBase, InstroAWG
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    ModulationType,
    Sine,
    Waveform,
)

# ---------------------------------------------------------------------------
# Minimal concrete driver — implements every abstract method with no-ops
# ---------------------------------------------------------------------------


class _MinimalAWGDriver(AWGDriverBase):
    """Satisfies the full abstract contract so we can test optional-method defaults."""

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def check_errors(self) -> None:
        pass

    def set_waveform(self, channel: int, waveform: Waveform) -> None:
        pass

    def get_waveform(self, channel: int) -> Waveform:
        return Sine(frequency_hz=1000.0)

    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
        pass

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        return (1.0, AmplitudeMeasurementUnit.VPP)

    def set_offset(self, channel: int, offset: float) -> None:
        pass

    def get_offset(self, channel: int) -> float:
        return 0.0

    def output_enable(self, channel: int, enable: bool) -> None:
        pass

    def get_output_state(self, channel: int) -> bool:
        return False


# ---------------------------------------------------------------------------
# AWGDriverBase contract tests
# ---------------------------------------------------------------------------


def test_01_awg_driver_base_contract_enforces_instantiation_rules() -> None:
    with pytest.raises(TypeError):
        AWGDriverBase()  # type: ignore[abstract]

    class _Incomplete(AWGDriverBase):
        def open(self) -> None:
            pass

        # all other abstract methods missing

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]

    assert isinstance(_MinimalAWGDriver(), AWGDriverBase)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_output_load", (1, 50.0)),
        ("get_output_load", (1,)),
        ("align_phase", ()),
        ("set_modulation", (1, ModulationType.AM, Sine(frequency_hz=1000.0), 0.5)),
        ("modulation_enable", (1, True)),
        ("get_modulation_type", (1,)),
        ("get_modulation_state", (1,)),
    ],
)
def test_02_awg_driver_base_optional_methods_raise_not_implemented(
    method_name: str,
    args: tuple[object, ...],
) -> None:
    driver = _MinimalAWGDriver()
    with pytest.raises(NotImplementedError, match=f"{method_name} is not implemented for _MinimalAWGDriver"):
        getattr(driver, method_name)(*args)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_driver() -> MagicMock:
    driver = MagicMock(spec=AWGDriverBase)
    driver.get_waveform.return_value = Sine(frequency_hz=1000.0)
    driver.get_amplitude.return_value = (2.5, AmplitudeMeasurementUnit.VPP)
    driver.get_offset.return_value = 0.0
    driver.get_output_state.return_value = False
    driver.get_output_load.return_value = 50.0
    driver.get_modulation_type.return_value = ModulationType.AM
    driver.get_modulation_state.return_value = False
    return driver


@pytest.fixture
def awg(mock_driver: MagicMock) -> InstroAWG:
    return InstroAWG(name="test_awg", driver=mock_driver, num_channels=2)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_03_lifecycle_open_close_start_background_daemon_and_invalid_configuration(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    awg.open()
    mock_driver.open.assert_called_once()
    awg.close()
    mock_driver.close.assert_called_once()

    registered = [(method, kwargs) for method, _, kwargs in awg._background_methods]
    assert registered == [
        (awg.get_output_state, {"channel": 1}),
        (awg.get_output_state, {"channel": 2}),
    ]

    with pytest.raises(ValueError, match="set_waveform must be called for at least one channel"):
        awg.start()

    # Unused channels must not block start(); the daemon's output-state poll needs no waveform.
    awg.set_waveform(1, Sine(frequency_hz=1000.0))
    awg.start()
    try:
        assert awg._background_thread is not None
        assert awg._background_thread.is_alive()
    finally:
        awg.close()

    with pytest.raises(ValueError, match="num_channels must be at least 1"):
        InstroAWG(name="test_awg", driver=mock_driver, num_channels=0)


# ---------------------------------------------------------------------------
# Error checking and tagging conventions
# ---------------------------------------------------------------------------


def test_04_check_errors_called_propagates_and_helper_tags_avoid_collision(
    awg: InstroAWG, mock_driver: MagicMock
) -> None:
    """Scope precedent: a pending error surfaces at the next query instead of hanging a later blocking read."""
    awg.set_waveform(1, Sine(frequency_hz=1000.0))
    mock_driver.check_errors.assert_called_once()

    mock_driver.check_errors.reset_mock()
    awg.get_output_state(1)
    awg.get_offset(1)
    assert mock_driver.check_errors.call_count == 2

    # Helper params are positional-only, so user tags named after them publish instead of colliding.
    cmd = awg.set_offset(1, 0.5, channel_suffix="rig7", value="x")
    assert cmd.tags["channel_suffix"] == "rig7"
    assert cmd.tags["value"] == "x"

    # Scope-style ordering: check_errors runs before config is recorded, so a rejected set is never cached.
    mock_driver.check_errors.side_effect = RuntimeError("instrument error queue not empty")
    with pytest.raises(RuntimeError, match="instrument error queue not empty"):
        awg.set_waveform(2, Sine(frequency_hz=1000.0))
    assert 2 not in awg._channel_waveforms


# ---------------------------------------------------------------------------
# Channel range validation — `awg` fixture is num_channels=2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_waveform", (Sine(frequency_hz=1000.0),)),
        ("get_waveform", ()),
        ("set_amplitude", (2.5, AmplitudeMeasurementUnit.VPP)),
        ("get_amplitude", ()),
        ("convert_amplitude", (2.5, AmplitudeMeasurementUnit.VPP, AmplitudeMeasurementUnit.VRMS)),
        ("set_offset", (0.5,)),
        ("get_offset", ()),
        ("output_enable", (True,)),
        ("get_output_state", ()),
        ("set_output_load", (50.0,)),
        ("get_output_load", ()),
        ("set_modulation", (ModulationType.AM, Sine(frequency_hz=1000.0), 0.5)),
        ("modulation_enable", (True,)),
        ("get_modulation_type", ()),
        ("get_modulation_state", ()),
    ],
)
@pytest.mark.parametrize("channel", [0, 1, 2, 3])
def test_05_channel_scoped_methods_respect_channel_bounds(
    awg: InstroAWG, method_name: str, args: tuple[object, ...], channel: int
) -> None:
    """Channels 1 and 2 are valid; 0 and 3 must raise regardless of which method is called."""
    if channel in (1, 2):
        awg.set_waveform(channel, Sine(frequency_hz=1000.0))  # convert_amplitude needs a configured waveform
        getattr(awg, method_name)(channel, *args)
    else:
        with pytest.raises(ValueError, match=f"channel {channel} out of range"):
            getattr(awg, method_name)(channel, *args)
