"""Publisher that plots Measurement/Command data on a Plotly figure."""

from datetime import datetime

import plotly.graph_objects as go  # type: ignore[import-untyped]

from instro.lib.types import Command, Measurement


class PlotlyPublisher:
    """Plot Measurement/Command data on a Plotly figure, one trace per channel.

    String values are skipped; they have no numeric value to plot.
    """

    def __init__(self, figure: go.Figure | None = None):
        """Add each channel's data to a trace named after the channel.

        Example:
            >>> import plotly.graph_objects as go
            >>> figure = go.FigureWidget()
            >>> publisher = PlotlyPublisher(figure)

        Args:
            figure: Figure to plot on. A ``go.FigureWidget`` updates live in a notebook.
                When omitted, the publisher creates its own figure and shows it on ``close()``.
        """
        self._owns_figure = figure is None
        self.figure = go.Figure() if figure is None else figure

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
        x = [datetime.fromtimestamp(t / 1e9) for t in timestamps]
        trace = next(self.figure.select_traces(selector={"name": channel}), None)
        if trace is None:
            self.figure.add_scatter(x=x, y=values, name=channel)
        else:
            trace.x = (*trace.x, *x)
            trace.y = (*trace.y, *values)

    def close(self) -> None:
        """Show the figure if this publisher created it."""
        if self._owns_figure:
            self.figure.show()
