"""Example: Publishers: live Plotly plot in a notebook.

Sweeps a simulated PSU and plots each reading live with PlotlyPublisher. This file is a
Jupytext percent notebook: each ``# %%`` line starts a cell.

Requires the plot extra. Start the simulated PSU first and leave it running:
    uv run python -m instro.psu.scpi_sim_server

Then open this file as a notebook in JupyterLab (right-click it, then Open With > Jupytext Notebook):
    uv run --extra plot --with jupyterlab --with jupytext --with anywidget jupyter lab examples/publishers/publish_plotly_notebook.py
"""

# %%
import time

from instro import InstroPSU
from instro.drivers import SimulatedPSU
from instro.lib import PlotlyPublisher

VISA_RESOURCE = "TCPIP0::127.0.0.1::5025::SOCKET"

# %% [markdown]
# Called with no figure, `PlotlyPublisher` creates a live `go.FigureWidget` and displays it below
# this cell. To style the plot, pass your own `go.FigureWidget` and display it instead.

# %%
plot_publisher = PlotlyPublisher()

psu = InstroPSU(
    name="myPSU",
    driver=SimulatedPSU(VISA_RESOURCE),
    num_channels=2,
    publishers=[plot_publisher],
)
psu.open()

# %% [markdown]
# Run the sweep and watch the figure above update as each reading arrives.

# %%
psu.set_current_limit(0.2, channel=1)
psu.output_enable(True, channel=1)

for v in range(10):
    psu.set_voltage(v, channel=1)
    time.sleep(0.5)
    psu.get_voltage(channel=1)
    psu.get_current(channel=1)

# %% [markdown]
# Turn the output off and close the PSU when you're done. Closing also writes any remaining
# points to the figure.

# %%
psu.output_enable(False, channel=1)
psu.close()
