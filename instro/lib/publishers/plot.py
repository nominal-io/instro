"""Publisher that plots Measurement/Command data on a Plotly figure."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np

from instro.lib.publishers.channel_buffer import NumpyRingBuffer
from instro.lib.publishers.publisher import Publisher
from instro.lib.types import Command, Measurement

# Plotly is the optional `plot` extra. The publisher only calls methods on the caller's
# figure, so importing instro never needs it.
if TYPE_CHECKING:
    import plotly.graph_objects as go  # type: ignore[import-untyped, import-not-found]


class PlotlyPublisher(Publisher):
    """Plot Measurement/Command data on a Plotly figure, one trace per channel.

    Each channel keeps its latest ``max_points`` points in a fixed-size ring buffer, and a
    background thread writes changed channels to the figure every ``flush_interval`` seconds, so
    a ``go.FigureWidget`` in a Jupyter notebook updates live. Times are plotted in UTC on a date x
    axis. String values are skipped; they have no numeric value to plot. Without a ``figure``, the
    publisher creates a ``go.FigureWidget`` and displays it in the notebook; it never shows a figure
    you pass in, so call ``figure.show()``, or display the widget, when you want to see it.

    Requires the ``plot`` extra (``pip install "instro[plot]"``).
    """

    def __init__(
        self, figure: go.Figure | go.FigureWidget | None = None, max_points: int = 10_000, flush_interval: float = 0.1
    ):
        """Create a publisher that plots each channel on a trace named after the channel.

        Args:
            figure: Figure to plot on. A ``go.FigureWidget`` updates live in a notebook. When
                omitted, the publisher creates a ``go.FigureWidget`` and displays it right away.
            max_points: Points kept per channel; older points drop off the plot.
            flush_interval: Seconds between writes to the figure.
        """
        if figure is None:
            import plotly.graph_objects as go  # type: ignore[import-untyped, import-not-found]
            from IPython.display import display  # type: ignore[import-untyped, import-not-found]

            figure = go.FigureWidget(layout={"xaxis": {"type": "date"}})
            display(figure)
        self.figure = figure
        # Set explicitly so the axis shows timestamps even before data arrives.
        self.figure.update_layout(xaxis_type="date")
        self._max_points = max_points
        self._flush_interval = flush_interval
        self._buffers: defaultdict[str, tuple[NumpyRingBuffer, NumpyRingBuffer]] = defaultdict(
            lambda: (NumpyRingBuffer(max_points, np.int64), NumpyRingBuffer(max_points, np.float64))
        )
        self._dirty: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flush_periodically, daemon=True)
        self._thread.start()

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        """Buffer ``data`` for the next write to the figure."""
        # Give each channel its own timestamps and values.
        batches: list[tuple[str, list[int], list]]
        if isinstance(data, Measurement):
            batches = [(channel, data.timestamps, values) for channel, values in data.channel_data.items()]
        else:
            batches = [(channel, [data.timestamp], [value]) for channel, value in data.channel_data.items()]

        # Add the numeric channels to their ring buffers and mark them for the next flush.
        with self._lock:
            for channel, timestamps, values in batches:
                if not values or isinstance(values[0], str):
                    continue
                channel_timestamps, channel_values = self._buffers[channel]
                channel_timestamps.extend(timestamps)
                channel_values.extend(values)
                self._dirty.add(channel)

    def _flush_periodically(self) -> None:
        while not self._stop.wait(self._flush_interval):
            self._flush()

    def _flush(self) -> None:
        # Copy the changed channels out of their ring buffers.
        pending = {}
        with self._lock:
            for channel in self._dirty:
                channel_timestamps, channel_values = self._buffers[channel]
                pending[channel] = (
                    channel_timestamps.get_latest(self._max_points),
                    channel_values.get_latest(self._max_points).tolist(),
                )
            self._dirty.clear()
        # Written outside the lock so a slow figure update never blocks publishers. Only the flush
        # thread, or close() after stopping it, writes to the figure.
        for channel, (timestamps, values) in pending.items():
            x = np.datetime_as_string(np.asarray(timestamps, dtype="datetime64[ns]"), unit="us")
            trace = next(self.figure.select_traces(selector={"name": channel}), None)
            if trace is None:
                self.figure.add_scatter(x=x, y=values, name=channel)
            else:
                trace.update(x=x, y=values)

    def close(self) -> None:
        """Stop the flush thread, write any remaining points to the figure, and clear the buffers."""
        self._stop.set()
        self._thread.join()
        self._flush()
        with self._lock:
            self._buffers.clear()
