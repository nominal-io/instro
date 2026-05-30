"""Tests for ModbusDevice autostart behavior.

Autostart is only meaningful when the config has a `timing` section (background polling).
These tests cover:
- Misuse (autostart=True without timing) raises at construction time
- Happy path (autostart=True + timing) opens the connection
- Default (autostart=False) does not open
"""

import asyncio
import threading
import time

import pytest
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer

from instro.lib.types import DeviceInfo
from instro.modbus import ModbusConfig, ModbusDevice, RegisterDef, TimingConfig

TEST_PORT = 5025


# ============ Sim Server ============


def _create_datastore() -> ModbusServerContext:
    store = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0, [False] * 10),
        co=ModbusSequentialDataBlock(0, [False] * 10),
        hr=ModbusSequentialDataBlock(0, [0] * 200),
        ir=ModbusSequentialDataBlock(0, [0] * 10),
    )
    return ModbusServerContext(devices={1: store}, single=False)


@pytest.fixture(scope="module")
def modbus_server():
    loop = asyncio.new_event_loop()
    context = _create_datastore()
    shutdown: asyncio.Event | None = None

    async def _run():
        nonlocal shutdown
        shutdown = asyncio.Event()
        server_task = asyncio.create_task(StartAsyncTcpServer(context=context, address=("127.0.0.1", TEST_PORT)))
        await shutdown.wait()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    def _thread_target():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    thread = threading.Thread(target=_thread_target, daemon=True)
    thread.start()
    time.sleep(0.3)
    yield
    assert shutdown is not None
    loop.call_soon_threadsafe(shutdown.set)
    thread.join(timeout=2.0)


# ============ Autostart Tests ============


class TestAutostart:
    def test_autostart_without_timing_raises(self, modbus_server):
        # autostart only makes sense when background polling is configured; misuse
        # should surface at construction time rather than silently opening a
        # connection that never gets driven.
        config = ModbusConfig(
            device=DeviceInfo(name="no_timing"),
            connection={"transport": "tcp", "host": "127.0.0.1", "port": TEST_PORT},
            registers=[RegisterDef(name="standalone", starting_address=100, data_type="uint16")],
        )
        with pytest.raises(ValueError, match="autostart=True requires"):
            ModbusDevice(config=config, autostart=True)

    def test_autostart_with_timing_opens_and_starts(self, modbus_server):
        # Happy path: timing section present, autostart wires up the daemon and opens.
        config = ModbusConfig(
            device=DeviceInfo(name="autostart_ok"),
            connection={"transport": "tcp", "host": "127.0.0.1", "port": TEST_PORT},
            timing=TimingConfig(poll_interval=1.0),
            registers=[RegisterDef(name="standalone", starting_address=100, data_type="uint16")],
        )
        dev = ModbusDevice(config=config, autostart=True)
        try:
            assert dev._client is not None  # open() ran
        finally:
            dev.close()

    def test_no_autostart_does_not_open(self, modbus_server):
        # Without autostart, no connection is opened even when timing is present.
        config = ModbusConfig(
            device=DeviceInfo(name="no_auto"),
            connection={"transport": "tcp", "host": "127.0.0.1", "port": TEST_PORT},
            timing=TimingConfig(poll_interval=1.0),
            registers=[RegisterDef(name="standalone", starting_address=100, data_type="uint16")],
        )
        dev = ModbusDevice(config=config)
        try:
            assert dev._client is None
        finally:
            dev.close()
