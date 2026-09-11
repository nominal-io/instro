"""Example: Publishers: publish to the live monitor.

Demonstrates streaming measurements to the `instro monitor` TUI (MonitorPublisher).

Start the simulated PSU and the monitor first, each in its own shell:
    python -m instro.psu.scpi_sim_server
    instro monitor

Then in a third shell:
    python examples/publishers/publish_monitor.py
"""

import time

from instro.lib.publishers import MonitorPublisher
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

VISA_RESOURCE = "TCPIP0::127.0.0.1::5025::SOCKET"

psu = InstroPSU(
    name="myPSU",
    driver=SimulatedPSU(VISA_RESOURCE),
    num_channels=2,
    publishers=[MonitorPublisher()],
)

with psu:
    psu.set_current_limit(0.2, channel=1)
    psu.output_enable(True, channel=1)

    # The background daemon polls the PSU; every measurement and command appears
    # in the monitor as soon as it is published.
    psu.start()
    for v in range(5):
        psu.set_voltage(v, channel=1)
        time.sleep(2.0)

    psu.output_enable(False, channel=1)
