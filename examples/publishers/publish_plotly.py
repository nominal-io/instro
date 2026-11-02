"""Example: Publishers: plot with Plotly.

Plots a simulated PSU voltage sweep with PlotlyPublisher. By default the publisher
creates its own figure and shows it when the PSU closes. Set ``USE_EXISTING_FIGURE``
to plot on a figure you build yourself instead.

Start the simulated PSU first:
    python -m instro.psu.scpi_sim_server

Then in another shell:
    python examples/publishers/publish_plotly.py
"""

import time

import plotly.graph_objects as go

from instro.lib.publishers import PlotlyPublisher
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

VISA_RESOURCE = "TCPIP0::127.0.0.1::5025::SOCKET"
USE_EXISTING_FIGURE = False  # True: plot on the figure built below. False: the publisher creates its own.

figure = go.Figure(layout_title_text="Simulated PSU voltage sweep") if USE_EXISTING_FIGURE else None

psu = InstroPSU(
    name="myPSU",
    driver=SimulatedPSU(VISA_RESOURCE),
    num_channels=2,
    publishers=[PlotlyPublisher(figure)],
)

with psu:
    psu.set_current_limit(0.2, channel=1)
    psu.output_enable(True, channel=1)

    for v in range(10):
        psu.set_voltage(v, channel=1)
        time.sleep(0.5)
        psu.get_voltage(channel=1)
        psu.get_current(channel=1)

    psu.output_enable(False, channel=1)

# The publisher only shows a figure it created; a figure passed in is the caller's to show.
if figure is not None:
    figure.show()
