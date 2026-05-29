"""Tests for ModbusRegisterDriver + InstroRegisterInstrument connection lifecycle.

Replaces the old autostart tests (autostart was a ModbusDevice-only concept that
no longer exists). Covers:
- Before open(): driver transport is not connected
- After open(): driver transport is connected
- After close(): driver transport is disconnected
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
from instro.register import InstroRegisterInstrument
from instro.register.drivers.modbus import ModbusConfig, ModbusRegisterDef, ModbusRegisterDriver, TimingConfig
from instro.utils.protocol.modbus import TCPConnectionConfig

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


def _make_config(name: str, *, with_timing: bool = False) -> ModbusConfig:
    return ModbusConfig(
        device=DeviceInfo(name=name),
        connection=TCPConnectionConfig(host="127.0.0.1", port=TEST_PORT),
        timing=TimingConfig(poll_interval=1.0) if with_timing else None,
        registers=[ModbusRegisterDef(name="standalone", starting_address=100, data_type="uint16")],
    )


# ============ Connection Lifecycle Tests ============


class TestConnectionLifecycle:
    def test_not_connected_before_open(self, modbus_server):
        config = _make_config("not_open")
        driver = ModbusRegisterDriver(config)
        dev = InstroRegisterInstrument(driver=driver)
        try:
            assert driver._modbus._client is None
        finally:
            dev.close()

    def test_connected_after_open(self, modbus_server):
        config = _make_config("after_open")
        driver = ModbusRegisterDriver(config)
        dev = InstroRegisterInstrument(driver=driver)
        dev.open()
        try:
            assert driver._modbus._client is not None
        finally:
            dev.close()

    def test_disconnected_after_close(self, modbus_server):
        config = _make_config("after_close")
        driver = ModbusRegisterDriver(config)
        dev = InstroRegisterInstrument(driver=driver)
        dev.open()
        assert driver._modbus._client is not None
        dev.close()
        assert driver._modbus._client is None

    def test_read_before_open_raises(self, modbus_server):
        config = _make_config("read_before_open")
        dev = InstroRegisterInstrument(driver=ModbusRegisterDriver(config))
        try:
            with pytest.raises(RuntimeError, match="not open"):
                dev.read("standalone")
        finally:
            dev.close()
