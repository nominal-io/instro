"""Tests for InstroRegisterInstrument background polling.

Verifies that the background daemon:
- reads grouped registers via read_group (one daemon call per group)
- reads ungrouped polled registers individually (one daemon call per register)
- excludes poll=False registers from the daemon
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

HR_REG_A = 42  # holding register at address 100
HR_REG_B = 17  # holding register at address 101
HR_REG_C = 99  # holding register at address 102


# ============ Sim Server ============


def _create_datastore() -> ModbusServerContext:
    # pymodbus FC3 maps starting_address N to data-block index N+1
    hr_values = [0] * 200
    hr_values[101] = HR_REG_A  # starting_address=100
    hr_values[102] = HR_REG_B  # starting_address=101
    hr_values[103] = HR_REG_C  # starting_address=102
    store = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0, [False] * 10),
        co=ModbusSequentialDataBlock(0, [False] * 10),
        hr=ModbusSequentialDataBlock(0, hr_values),
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


# ============ Background Polling Tests ============


class TestBackgroundPolling:
    def test_grouped_registers_produce_channel_data(self, modbus_server):
        config = ModbusConfig(
            device=DeviceInfo(name="bg_test"),
            connection=TCPConnectionConfig(host="127.0.0.1", port=TEST_PORT),
            timing=TimingConfig(poll_interval=0.05),
            registers=[
                ModbusRegisterDef(name="reg_a", starting_address=100, data_type="uint16", read_group="g1"),
                ModbusRegisterDef(name="reg_b", starting_address=101, data_type="uint16", read_group="g1"),
            ],
        )
        driver = ModbusRegisterDriver(config, thread_safe=True)
        device = InstroRegisterInstrument(driver=driver)
        device.background_interval = 0.1
        device.open()
        device.start()
        try:
            meas_a = device.get_channel("bg_test.reg_a", wait_for_latest=True, timeout=5.0)
            meas_b = device.get_channel("bg_test.reg_b", wait_for_latest=True, timeout=5.0)
            assert meas_a.channel_data["bg_test.reg_a"][0] == HR_REG_A
            assert meas_b.channel_data["bg_test.reg_b"][0] == HR_REG_B
        finally:
            device.stop()
            device.close()

    def test_ungrouped_polled_register_produces_channel_data(self, modbus_server):
        config = ModbusConfig(
            device=DeviceInfo(name="bg_test"),
            connection=TCPConnectionConfig(host="127.0.0.1", port=TEST_PORT),
            timing=TimingConfig(poll_interval=0.05),
            registers=[
                ModbusRegisterDef(name="reg_c", starting_address=102, data_type="uint16"),
            ],
        )
        driver = ModbusRegisterDriver(config, thread_safe=True)
        device = InstroRegisterInstrument(driver=driver)
        device.background_interval = 0.1
        device.open()
        device.start()
        try:
            meas = device.get_channel("bg_test.reg_c", wait_for_latest=True, timeout=5.0)
            assert meas.channel_data["bg_test.reg_c"][0] == HR_REG_C
        finally:
            device.stop()
            device.close()

    def test_poll_false_register_not_in_daemon(self):
        config = ModbusConfig(
            device=DeviceInfo(name="bg_test"),
            connection=TCPConnectionConfig(host="127.0.0.1", port=TEST_PORT),
            registers=[
                ModbusRegisterDef(name="polled", starting_address=100, data_type="uint16"),
                ModbusRegisterDef(name="not_polled", starting_address=101, data_type="uint16", poll=False),
            ],
        )
        driver = ModbusRegisterDriver(config, thread_safe=True)
        device = InstroRegisterInstrument(driver=driver)
        daemon_fn_names = [method.__name__ for method, _, _ in device._background_methods]
        daemon_args = [args for _, args, _ in device._background_methods]
        assert daemon_fn_names == ["read"]
        assert daemon_args == [("polled",)]
