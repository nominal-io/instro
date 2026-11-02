"""Unit tests for PlotlyPublisher trace building and figure ownership."""

from datetime import timedelta
from unittest.mock import patch

import plotly.graph_objects as go  # type: ignore[import-untyped]

from instro.lib.publishers import PlotlyPublisher
from instro.lib.types import Command, Measurement

T0 = 1_700_000_000_000_000_000  # ns since the Unix epoch
ONE_SECOND = 1_000_000_000


def test_measurements_append_to_one_trace_per_channel():
    publisher = PlotlyPublisher()

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0, 2.0]}, timestamps=[T0, T0 + ONE_SECOND]))
    publisher.publish(Measurement(channel_data={"psu.voltage": [3.0]}, timestamps=[T0 + 2 * ONE_SECOND]))

    (trace,) = publisher.figure.data
    assert trace.name == "psu.voltage"
    assert trace.y == (1.0, 2.0, 3.0)
    assert trace.x[2] - trace.x[0] == timedelta(seconds=2)


def test_command_plots_one_point_on_its_channel():
    publisher = PlotlyPublisher()

    publisher.publish(Command(channel_data={"psu.voltage.cmd": 5.0}, timestamp=T0))

    (trace,) = publisher.figure.data
    assert trace.name == "psu.voltage.cmd"
    assert trace.y == (5.0,)


def test_string_and_empty_channels_are_skipped():
    publisher = PlotlyPublisher()

    publisher.publish(Measurement(channel_data={"psu.mode": ["CV"]}, timestamps=[T0]))
    publisher.publish(Measurement(channel_data={"psu.voltage": []}, timestamps=[]))
    publisher.publish(Command(channel_data={"psu.mode.cmd": "CC"}, timestamp=T0))

    assert publisher.figure.data == ()


def test_only_a_self_created_figure_is_shown_on_close():
    existing = go.Figure(go.Scatter(x=[0], y=[0], name="existing"))
    passed_in = PlotlyPublisher(existing)
    passed_in.publish(Measurement(channel_data={"dmm.voltage": [0.5]}, timestamps=[T0]))
    self_created = PlotlyPublisher()

    with patch.object(go.Figure, "show") as show:
        passed_in.close()
        show.assert_not_called()
        self_created.close()
        show.assert_called_once()

    assert passed_in.figure is existing
    assert [t.name for t in existing.data] == ["existing", "dmm.voltage"]
