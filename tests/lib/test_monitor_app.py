"""Headless test that the monitor TUI tracks MonitorState through add, update, disconnect, and clear."""

import asyncio

from textual.widgets import DataTable

from instro.lib.consumers.monitor.app import MonitorApp
from instro.lib.consumers.monitor.server import MonitorState
from instro.lib.types import Command, Measurement

SOURCE = "127.0.0.1:1"


def test_table_follows_state_changes():
    state = MonitorState()
    state.source_connected(SOURCE)
    state.ingest(
        SOURCE, Measurement(channel_data={"psu.ch1.voltage": [1.0, 2.5], "psu.ch1.mode": ["CV"]}, timestamps=[1, 2])
    )
    app = MonitorApp(state, "127.0.0.1:0")

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one(DataTable)
            assert table.row_count == 2
            assert str(table.get_cell("psu.ch1.voltage", "value")) == "2.5"

            state.ingest(SOURCE, Command(channel_data={"psu.ch1.voltage.cmd": 3.0}, timestamp=3))
            state.ingest(SOURCE, Measurement(channel_data={"psu.ch1.voltage": [9.0]}, timestamps=[4]))
            await pilot.pause(0.4)
            assert table.row_count == 3
            assert str(table.get_cell("psu.ch1.voltage", "value")) == "9"
            assert str(table.get_cell("psu.ch1.voltage.cmd", "kind")) == "C"

            state.source_disconnected(SOURCE)
            await pilot.pause(0.4)
            assert "(gone)" in str(table.get_cell("psu.ch1.voltage", "source"))

            await pilot.press("c")
            await pilot.pause(0.4)
            assert table.row_count == 0

    asyncio.run(scenario())
