"""Live synchronization of measurement data with Plotly.

Meant to be run in a jupyter notebook
"""

# Initialize Reader
from pathlib import Path

from instro.unstable.lib.consumers import FileConsumer
from instro.unstable.lib.sinks import PlotlyLiveSink

file_path = Path("/tmp/instro/meas.jsonl")
consumer = FileConsumer(file_path=file_path, poll_s=0.1)

# Initialize Sink
plot_sink = PlotlyLiveSink(window=50)
plot_sink.display()

# Connect and run
plot_sink.start(consumer)
