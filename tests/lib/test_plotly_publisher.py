"""Unit tests for PlotlyPublisher buffering and flushing."""

import threading
import time
from unittest.mock import patch

import pandas as pd  # type: ignore[import-untyped]
import plotly.graph_objects as go  # type: ignore[import-untyped]

from instro.lib.publishers import PlotlyPublisher
from instro.lib.types import Command, Measurement

T0 = 1_700_000_000_000_000_000  # ns since the Unix epoch
ONE_SECOND = 1_000_000_000


def test_measurements_append_to_one_trace_per_channel():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure)

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0, 2.0]}, timestamps=[T0, T0 + ONE_SECOND]))
    publisher.publish(Measurement(channel_data={"psu.voltage": [3.0]}, timestamps=[T0 + 2 * ONE_SECOND]))
    publisher.close()

    (trace,) = figure.data
    assert trace.name == "psu.voltage"
    assert trace.y == (1.0, 2.0, 3.0)
    assert list(trace.x) == ["2023-11-14T22:13:20.000000", "2023-11-14T22:13:21.000000", "2023-11-14T22:13:22.000000"]


def test_command_plots_one_point_on_its_channel():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure)

    publisher.publish(Command(channel_data={"psu.voltage.cmd": 5.0}, timestamp=T0))
    publisher.close()

    (trace,) = figure.data
    assert trace.name == "psu.voltage.cmd"
    assert trace.y == (5.0,)


def test_string_and_empty_channels_are_skipped():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure)

    publisher.publish(Measurement(channel_data={"psu.mode": ["CV"]}, timestamps=[T0]))
    publisher.publish(Measurement(channel_data={"psu.voltage": []}, timestamps=[]))
    publisher.publish(Command(channel_data={"psu.mode.cmd": "CC"}, timestamp=T0))
    publisher.close()

    assert figure.data == ()


def test_existing_traces_on_the_figure_are_kept():
    figure = go.Figure(go.Scatter(x=[0], y=[0], name="existing"))
    publisher = PlotlyPublisher(figure)

    publisher.publish(Measurement(channel_data={"dmm.voltage": [0.5]}, timestamps=[T0]))
    publisher.close()

    assert [t.name for t in figure.data] == ["existing", "dmm.voltage"]


def test_empty_matching_trace_on_the_figure_is_filled():
    figure = go.Figure(go.Scatter(name="psu.voltage"))
    publisher = PlotlyPublisher(figure)

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0]}, timestamps=[T0]))
    publisher.close()

    (trace,) = figure.data
    assert trace.y == (1.0,)
    assert len(trace.x) == 1


def test_without_a_figure_a_figure_widget_is_created_and_displayed():
    axis_types_at_display = []
    with patch(
        "IPython.display.display", side_effect=lambda f: axis_types_at_display.append(f.layout.xaxis.type)
    ) as display:
        publisher = PlotlyPublisher()
    display.assert_called_once_with(publisher.figure)
    assert axis_types_at_display == ["date"]

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0]}, timestamps=[T0]))
    with patch("plotly.io.show") as show:
        publisher.close()
    show.assert_called_once_with(publisher.figure)

    assert isinstance(publisher.figure, go.FigureWidget)
    (trace,) = publisher.figure.data
    assert trace.y == (1.0,)


def test_figure_passed_in_is_never_shown():
    publisher = PlotlyPublisher(go.Figure())

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0]}, timestamps=[T0]))
    with patch("plotly.io.show") as show:
        publisher.close()

    show.assert_not_called()


def test_x_axis_always_shows_timestamps():
    figure = go.Figure(layout={"xaxis": {"type": "linear"}})
    PlotlyPublisher(figure).close()

    assert figure.layout.xaxis.type == "date"


def test_mismatched_timestamps_do_not_shift_later_points():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure)

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0, 2.0, 3.0]}, timestamps=[T0, T0 + ONE_SECOND]))
    publisher.publish(Measurement(channel_data={"psu.voltage": [4.0]}, timestamps=[T0 + 3 * ONE_SECOND]))
    publisher.close()

    (trace,) = figure.data
    assert trace.y == (1.0, 2.0, 3.0, 4.0)
    assert list(trace.x) == [
        "2023-11-14T22:13:20.000000",
        "2023-11-14T22:13:21.000000",
        "2023-11-14T22:13:21.000000",
        "2023-11-14T22:13:23.000000",
    ]


def test_time_window_starts_at_the_first_point():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure, time_window="1min")

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0, 2.0]}, timestamps=[T0, T0 + 10 * ONE_SECOND]))
    publisher.close()

    assert figure.layout.xaxis.range == ("2023-11-14T22:13:20.000000", "2023-11-14T22:14:20.000000")


def test_time_window_scrolls_forward_once_the_newest_point_passes_its_end():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure, time_window=pd.Timedelta(minutes=1))

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0, 2.0]}, timestamps=[T0, T0 + 90 * ONE_SECOND]))
    publisher.close()

    assert figure.layout.xaxis.range == ("2023-11-14T22:13:50.000000", "2023-11-14T22:14:50.000000")


def test_without_a_time_window_the_x_axis_range_is_not_set():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure, time_window=None)

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0, 2.0]}, timestamps=[T0, T0 + 90 * ONE_SECOND]))
    publisher.close()

    assert figure.layout.xaxis.range is None


def test_each_channel_keeps_only_its_latest_max_points():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure, max_points=3)

    publisher.publish(
        Measurement(channel_data={"psu.voltage": [1.0, 2.0, 3.0, 4.0, 5.0]}, timestamps=[T0 + i for i in range(5)])
    )
    publisher.close()

    (trace,) = figure.data
    assert trace.y == (3.0, 4.0, 5.0)
    assert len(trace.x) == 3


def test_points_reach_the_figure_before_close():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure, flush_interval=0.01)

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0]}, timestamps=[T0]))
    deadline = time.monotonic() + 5
    while not figure.data and time.monotonic() < deadline:
        time.sleep(0.01)
    flushed = [t.name for t in figure.data]
    publisher.close()

    assert flushed == ["psu.voltage"]


def test_concurrent_publishes_keep_every_point_on_one_trace():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure, flush_interval=0.001)
    num_threads, publishes = 8, 50
    barrier = threading.Barrier(num_threads)

    def publish_many():
        barrier.wait()
        for i in range(publishes):
            publisher.publish(Measurement(channel_data={"psu.voltage": [1.0]}, timestamps=[T0 + i]))

    threads = [threading.Thread(target=publish_many) for _ in range(num_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    publisher.close()

    (trace,) = figure.data
    assert len(trace.x) == len(trace.y) == num_threads * publishes
