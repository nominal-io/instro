"""Unit tests for protocol scaling types (LinearScale)."""

import pytest

from instro.lib.types import LinearScale


class TestLinearScale:
    def test_to_physical(self):
        scale = LinearScale(gain=0.1, offset=5.0)
        assert scale.to_physical(1000) == pytest.approx(105.0)

    def test_to_raw(self):
        scale = LinearScale(gain=0.1, offset=5.0)
        assert scale.to_raw(105.0) == pytest.approx(1000.0)

    def test_roundtrip(self):
        scale = LinearScale(gain=2.5, offset=-10.0)
        for raw in (0, 100, 255, 65535):
            physical = scale.to_physical(raw)
            assert scale.to_raw(physical) == pytest.approx(raw)

    def test_zero_gain_rejected(self):
        with pytest.raises(ValueError, match="gain must not be zero"):
            LinearScale(gain=0)
