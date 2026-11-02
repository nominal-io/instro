"""Publisher that plots Measurement/Command data on a Plotly figure."""

import threading
from datetime import datetime, timezone

import plotly.graph_objects as go  # type: ignore[import-untyped]

from instro.lib.types import Command, Measurement


class PlotlyPublisher:
    """Plot Measurement/Command data on a Plotly figure, one trace per channel.

    Times are plotted in UTC. String values are skipped; they have no numeric value to plot.
    The publisher never shows the figure; call ``figure.show()`` when you want to see it.
    """

    def __init__(self, figure: go.Figure):
        """Add each channel's data to a trace named after the channel.

        Example:
            >>> import plotly.graph_objects as go
            >>> figure = go.Figure()
            >>> publisher = PlotlyPublisher(figure)

        Args:
            figure: Figure to plot on. A ``go.FigureWidget`` updates live in a notebook.
        """
        self.figure = figure
        self._lock = threading.Lock()

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        """Append ``data`` to its channels' traces."""
        if isinstance(data, Measurement):
            for channel, values in data.channel_data.items():
                self._extend(channel, data.timestamps, values)
        elif isinstance(data, Command):
            for channel, value in data.channel_data.items():
                self._extend(channel, [data.timestamp], [value])

    def _extend(self, channel: str, timestamps: list[int], values: list) -> None:
        if not values or isinstance(values[0], str):
            return
        x = [datetime.fromtimestamp(t / 1e9, tz=timezone.utc) for t in timestamps]
        # Background polling and foreground reads publish from different threads.
        with self._lock:
            trace = next(self.figure.select_traces(selector={"name": channel}), None)
            if trace is None:
                self.figure.add_scatter(x=x, y=values, name=channel)
            else:
                # Unset coordinates on a caller's placeholder trace are None.
                old_x = () if trace.x is None else trace.x
                old_y = () if trace.y is None else trace.y
                trace.update(x=(*old_x, *x), y=(*old_y, *values))

    def close(self) -> None:
        """No-op; the caller owns the figure."""
        pass
