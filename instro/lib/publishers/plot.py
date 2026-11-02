"""Publisher that plots Measurement/Command data on a Plotly figure."""

from __future__ import annotations

import threading
from collections import Counter
from typing import TYPE_CHECKING

import numpy as np

from instro.lib.publishers.channel_buffer import NumpyInMemoryPublisher
from instro.lib.types import Command, Measurement

# Plotly is the optional `plot` extra. The publisher only calls methods on the caller's
# figure, so importing instro never needs it.
if TYPE_CHECKING:
    import plotly.graph_objects as go  # type: ignore[import-untyped, import-not-found]


class PlotlyPublisher(NumpyInMemoryPublisher):
    """Plot Measurement/Command data on a Plotly figure, one trace per channel.

    Each channel keeps its latest ``max_points`` points in a fixed-size ring buffer, and a
    background thread writes changed channels to the figure every ``flush_interval`` seconds, so
    a ``go.FigureWidget`` in a Jupyter notebook updates live. Times are plotted in UTC on a date x
    axis. String values are skipped; they have no numeric value to plot. Without a ``figure``, the
    publisher creates a ``go.FigureWidget`` and displays it in the notebook; it never shows a figure
    you pass in, so call ``figure.show()``, or display the widget, when you want to see it.

    Requires the ``plot`` extra (``pip install "instro[plot]"``).
    """

    def __init__(self, figure: go.Figure | None = None, max_points: int = 10_000, flush_interval: float = 0.1):
        """Create a publisher that plots each channel on a trace named after the channel.

        Args:
            figure: Figure to plot on. A ``go.FigureWidget`` updates live in a notebook. When
                omitted, the publisher creates a ``go.FigureWidget`` and displays it right away.
            max_points: Points kept per channel; older points drop off the plot.
            flush_interval: Seconds between writes to the figure.
        """
        super().__init__(max_points, value_dtype=np.float64)
        if figure is None:
            import plotly.graph_objects as go  # type: ignore[import-untyped, import-not-found]
            from IPython.display import display  # type: ignore[import-untyped, import-not-found]

            figure = go.FigureWidget(layout={"xaxis": {"type": "date"}})
            display(figure)
        self.figure = figure
        # Set explicitly so the axis shows timestamps even before data arrives.
        self.figure.update_layout(xaxis_type="date")
        self._flush_interval = flush_interval
        self._flushed_count: Counter[str] = Counter()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flush_periodically, daemon=True)
        self._thread.start()

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        """Buffer ``data`` for the next write to the figure."""
        if isinstance(data, Measurement):
            for channel, values in data.channel_data.items():
                self._append(channel, data.timestamps, values)
        elif isinstance(data, Command):
            for channel, value in data.channel_data.items():
                self._append(channel, [data.timestamp], [value])

    def _append(self, channel: str, timestamps: list[int], values: list) -> None:
        if not values or not timestamps or isinstance(values[0], str):
            return
        # The base class extends value and timestamp buffers separately, so equalize their lengths first.
        timestamps = timestamps[: len(values)] + [timestamps[-1]] * (len(values) - len(timestamps))
        super().publish(Measurement(channel_data={channel: values}, timestamps=timestamps))

    def _flush_periodically(self) -> None:
        while not self._stop.wait(self._flush_interval):
            self._flush()

    def _flush(self) -> None:
        pending = {}
        with self._condition:
            for channel in self._values:
                if self._total_added_count[channel] == self._flushed_count[channel]:
                    continue
                self._flushed_count[channel] = self._total_added_count[channel]
                n = self._points_in_buffer(channel)
                pending[channel] = (self._get_timestamps(channel, n), self._get_values(channel, n))
        # Written outside the lock so a slow figure update never blocks publishers. Only the flush
        # thread, or close() after stopping it, writes to the figure.
        for channel, (timestamps, values) in pending.items():
            # UTC ISO strings, which plotly.js draws as written. Epoch-ms numbers would be shown in the
            # viewer's local time (repeating across DST changes); datetime64[ns] reaches a FigureWidget as
            # raw nanosecond integers.
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
        super().close()
