import time
from collections import deque
from typing import Any

import numpy as np
import plotly.graph_objects as go
from plotly.graph_objs._figurewidget import FigureWidget
from typing import Union, Optional

from instro.lib.types import Measurement

from .sink import Sink


class PlotlyLiveSink(Sink):
    """A Sink that updates a Plotly FigureWidget with incoming measurements."""

    def __init__(self, window: int = 50, poll_s: float = 0.01, fig: FigureWidget | None = None):
        super().__init__()
        self.window = window
        self.poll_s = poll_s
        self.channel_state = {}

        self.fig = fig if fig is not None else go.FigureWidget(
            layout=dict(
                xaxis=dict(type="date", tickformat="%H:%M:%S", tickangle=-45),
                autosize=True,
                height=300,
                margin=dict(l=10, r=10, b=60, t=60),
            )
        )

    def display(self):
        """Displays the widget in the Jupyter notebook."""
        display(self.fig)

    def process_item(self, m: Any) -> None:
        """Processes a single item and updates the Plotly figure."""
        # from your_module import Measurement
        if not isinstance(m, Measurement):
            return  # Skip commands or unsupported objects

        parsed_times = np.array(m.timestamps, dtype="datetime64[ns]")

        for channel_name, vals in m.channel_data.items():
            # Create trace if it doesn't exist
            if channel_name not in self.channel_state:
                self.fig.add_scatter(mode="lines+markers", name=channel_name)
                self.channel_state[channel_name] = {
                    "times": deque(maxlen=self.window),
                    "values": deque(maxlen=self.window),
                    "idx": len(self.fig.data) - 1,
                }

            # Append new data
            self.channel_state[channel_name]["times"].extend(parsed_times)
            self.channel_state[channel_name]["values"].extend(vals)

        # Batch update the Plotly UI
        with self.fig.batch_update():
            for ch_data in self.channel_state.values():
                self.fig.data[ch_data["idx"]].x = tuple(ch_data["times"])
                self.fig.data[ch_data["idx"]].y = tuple(ch_data["values"])

        # Give the Jupyter front-end a tiny window to render the update
        time.sleep(self.poll_s)
