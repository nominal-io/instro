"""Unit tests for PlotlyPublisher trace building."""

import threading
from datetime import timedelta

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

    (trace,) = figure.data
    assert trace.name == "psu.voltage"
    assert trace.y == (1.0, 2.0, 3.0)
    assert trace.x[2] - trace.x[0] == timedelta(seconds=2)


def test_command_plots_one_point_on_its_channel():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure)

    publisher.publish(Command(channel_data={"psu.voltage.cmd": 5.0}, timestamp=T0))

    (trace,) = figure.data
    assert trace.name == "psu.voltage.cmd"
    assert trace.y == (5.0,)


def test_string_and_empty_channels_are_skipped():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure)

    publisher.publish(Measurement(channel_data={"psu.mode": ["CV"]}, timestamps=[T0]))
    publisher.publish(Measurement(channel_data={"psu.voltage": []}, timestamps=[]))
    publisher.publish(Command(channel_data={"psu.mode.cmd": "CC"}, timestamp=T0))

    assert figure.data == ()


def test_existing_traces_on_the_figure_are_kept():
    figure = go.Figure(go.Scatter(x=[0], y=[0], name="existing"))
    publisher = PlotlyPublisher(figure)

    publisher.publish(Measurement(channel_data={"dmm.voltage": [0.5]}, timestamps=[T0]))

    assert [t.name for t in figure.data] == ["existing", "dmm.voltage"]


def test_empty_matching_trace_on_the_figure_is_filled():
    figure = go.Figure(go.Scatter(name="psu.voltage"))
    publisher = PlotlyPublisher(figure)

    publisher.publish(Measurement(channel_data={"psu.voltage": [1.0]}, timestamps=[T0]))

    (trace,) = figure.data
    assert trace.y == (1.0,)
    assert len(trace.x) == 1


def test_mismatched_timestamps_keep_x_and_y_the_same_length():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure)

    publisher.publish(Measurement(channel_data={"short": [1.0, 2.0, 3.0]}, timestamps=[T0, T0 + ONE_SECOND]))
    publisher.publish(Measurement(channel_data={"long": [1.0]}, timestamps=[T0, T0 + ONE_SECOND]))

    short, long = figure.data
    assert len(short.x) == len(short.y) == 3
    assert short.x[2] == short.x[1]
    assert len(long.x) == len(long.y) == 1


def test_concurrent_publishes_keep_every_point_on_one_trace():
    figure = go.Figure()
    publisher = PlotlyPublisher(figure)
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

    (trace,) = figure.data
    assert len(trace.x) == len(trace.y) == num_threads * publishes
