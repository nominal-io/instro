"""Feature test: two ModbusDevices share one transport, each bound to its own unit via at() (GH #262).

Encodes the primary user story end-to-end: construct one ModbusTCPTransport, bind an address to it
per device with ``bus.at(...)``, and have each read and write only its own unit over the one session
— surviving the first device's close and released by the last. Also pins the shape the addresses now
live in: no unit_id on a transport, ``modbus_unit_id`` on the device, and no
ModbusDriver/TCPConnection/RTUConnection. A second, narrow scenario proves the same one-session
invariant on the serial path with a mocked pymodbus client, since CI has no RS-485 line.
Fails until the transport classes exist.
"""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer

from instro import modbus as modbus_package
from instro.lib.transports import modbus as modbus_transport
from instro.lib.transports.modbus import ModbusRTUTransport, ModbusTCPTransport
from instro.modbus import ModbusDevice

HOST = "127.0.0.1"
TEST_PORT = 5032

FLOW_UNIT = 3
PUMP_UNIT = 7
FLOW_VALUE = 1111
PUMP_VALUE = 2222

SETPOINT_ADDRESS = 100

# One config shape for every device; only the unit address differs.
DEVICE_CONFIG = {
    "version": 1,
    "protocol": "modbus",
    "device": {"name": "shared_bus_device"},
    "registers": [
        {
            "name": "setpoint",
            "starting_address": SETPOINT_ADDRESS,
            "register_type": "holding",
            "data_type": "uint16",
        }
    ],
}


def _device_store(setpoint: int) -> ModbusDeviceContext:
    """One simulated unit holding ``setpoint`` at ``SETPOINT_ADDRESS``."""
    holding = [0] * (SETPOINT_ADDRESS + 10)
    holding[SETPOINT_ADDRESS] = setpoint
    # ModbusDeviceContext has a +1 offset quirk, so prepend a dummy so Modbus
    # address N maps to array index N+1.
    return ModbusDeviceContext(hr=ModbusSequentialDataBlock(0, [0] + holding))


@pytest.fixture(scope="module")
def two_unit_server():
    """A sim Modbus TCP server answering at two unit addresses on one listening socket."""
    context = ModbusServerContext(
        devices={FLOW_UNIT: _device_store(FLOW_VALUE), PUMP_UNIT: _device_store(PUMP_VALUE)},
        single=False,
    )
    loop = asyncio.new_event_loop()
    shutdown: asyncio.Event | None = None

    async def _run():
        nonlocal shutdown
        shutdown = asyncio.Event()
        server_task = asyncio.create_task(StartAsyncTcpServer(context=context, address=(HOST, TEST_PORT)))
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
    time.sleep(0.3)  # wait for the server to bind
    yield
    assert shutdown is not None
    loop.call_soon_threadsafe(shutdown.set)
    thread.join(timeout=2.0)


def test_two_devices_share_one_connection_bound_by_at(two_unit_server):
    for retired in ("ModbusDriver", "TCPConnection", "RTUConnection", "ConnectionType"):
        assert not hasattr(modbus_transport, retired)
    for retired in ("TCPConnection", "RTUConnection"):
        assert not hasattr(modbus_package, retired)

    with pytest.raises(TypeError):
        ModbusTCPTransport(host=HOST, port=TEST_PORT, unit_id=5)

    shared_bus = ModbusTCPTransport(host=HOST, port=TEST_PORT)
    assert not hasattr(shared_bus, "unit_id")
    assert not hasattr(shared_bus, "modbus_unit_id")

    # A stale connection-block unit_id must name where it moved, not just fail as an extra key.
    with pytest.raises(ValidationError, match="moved") as stale:
        ModbusDevice(
            config=DEVICE_CONFIG,
            connection={"transport": "tcp", "host": HOST, "port": TEST_PORT, "unit_id": 5},
        )
    assert "modbus_unit_id" in str(stale.value)

    # A bare (possibly shared) transport with no address is an error, never a silent unit 1.
    # ValidationError subclasses ValueError, so match= is what makes this a "named error" gate.
    with pytest.raises(ValueError, match="modbus_unit_id"):
        ModbusDevice(config=DEVICE_CONFIG, connection=shared_bus, name="unaddressed")

    # Binding the address twice over is ambiguous about which one wins.
    with pytest.raises(ValueError, match="modbus_unit_id"):
        ModbusDevice(
            config=DEVICE_CONFIG,
            connection=shared_bus.at(FLOW_UNIT),
            name="doubly_addressed",
            modbus_unit_id=PUMP_UNIT,
        )

    flow_meter = ModbusDevice(config=DEVICE_CONFIG, connection=shared_bus.at(FLOW_UNIT), name="flow_meter")
    pump = ModbusDevice(config=DEVICE_CONFIG, connection=shared_bus.at(PUMP_UNIT), name="pump")

    flow_meter.open()
    pump.open()

    assert shared_bus.is_open
    assert (flow_meter.modbus_unit_id, pump.modbus_unit_id) == (FLOW_UNIT, PUMP_UNIT)

    # Required per call on the transport, on an open session: omission is a TypeError, never unit 1.
    with pytest.raises(TypeError):
        shared_bus.read_holding_registers(SETPOINT_ADDRESS, 1)
    with pytest.raises(TypeError):
        shared_bus.read_typed("holding", SETPOINT_ADDRESS, "uint16")

    # A bound unit carries its address and refuses a second one.
    bound_flow = shared_bus.at(FLOW_UNIT)
    assert bound_flow.read_holding_registers(SETPOINT_ADDRESS, 1) == [FLOW_VALUE]
    with pytest.raises(TypeError):
        bound_flow.read_holding_registers(SETPOINT_ADDRESS, 1, unit_id=PUMP_UNIT)

    assert flow_meter.read("setpoint").latest == FLOW_VALUE
    assert pump.read("setpoint").latest == PUMP_VALUE

    pump.write("setpoint", 4242)
    assert pump.read("setpoint").latest == 4242
    assert flow_meter.read("setpoint").latest == FLOW_VALUE

    # The holder refcount, not is_open, is what keeps the session up for the surviving holder.
    flow_meter.close()
    assert shared_bus.is_open
    assert pump.read("setpoint").latest == 4242

    pump.close()
    assert not shared_bus.is_open

    # A device that builds its own private transport may default to unit 1's stand-in address.
    solo = ModbusDevice(
        config=DEVICE_CONFIG,
        connection={"transport": "tcp", "host": HOST, "port": TEST_PORT},
        name="solo",
        modbus_unit_id=FLOW_UNIT,
    )
    solo.open()
    assert solo.read("setpoint").latest == FLOW_VALUE
    solo.close()


def test_modbus_unit_id_conflict_between_constructor_and_config_is_rejected(two_unit_server):
    config_with_address = {**DEVICE_CONFIG, "modbus_unit_id": FLOW_UNIT}
    connection = {"transport": "tcp", "host": HOST, "port": TEST_PORT}

    # Constructor kwarg disagrees with the config field: a named error, not a silent override.
    with pytest.raises(ValueError, match="modbus_unit_id"):
        ModbusDevice(config=config_with_address, connection=connection, modbus_unit_id=PUMP_UNIT)

    # Agreeing values are not a conflict.
    device = ModbusDevice(config=config_with_address, connection=connection, modbus_unit_id=FLOW_UNIT)
    assert device.modbus_unit_id == FLOW_UNIT


def test_modbus_unit_id_setter_rejects_reassignment_while_open(two_unit_server):
    shared_bus = ModbusTCPTransport(host=HOST, port=TEST_PORT)
    device = ModbusDevice(config=DEVICE_CONFIG, connection=shared_bus.at(FLOW_UNIT), name="reassign_check")

    device.modbus_unit_id = PUMP_UNIT  # closed: free to rebind
    assert device.modbus_unit_id == PUMP_UNIT

    device.open()
    try:
        with pytest.raises(RuntimeError, match="modbus_unit_id"):
            device.modbus_unit_id = FLOW_UNIT
        assert device.modbus_unit_id == PUMP_UNIT  # rejected attempt left the binding untouched
    finally:
        device.close()


def test_two_devices_share_one_serial_port():
    """The RS-485 half: one ModbusRTUTransport, two devices, exactly one serial client constructed."""
    # Patched at the module attribute, not at pymodbus.client: the transport imports the client
    # class at module scope, so instro's own binding is what has to be replaced. Same form as
    # tests/lib/test_visa_driver.py's patch of instro.lib.transports.visa.pyvisa.ResourceManager.
    with patch("instro.lib.transports.modbus.ModbusSerialClient") as serial_client:
        serial_client.return_value.connect.return_value = True

        bus = ModbusRTUTransport(port="/dev/ttyUSB0", baudrate=19200)
        flow = ModbusDevice(config=DEVICE_CONFIG, connection=bus.at(FLOW_UNIT), name="flow_meter")
        pump = ModbusDevice(config=DEVICE_CONFIG, connection=bus.at(PUMP_UNIT), name="pump")

        flow.open()
        pump.open()

        # pymodbus opens serial exclusive=True, so a second construction is the bug this fixes.
        assert serial_client.call_count == 1
        assert bus.is_open

        flow.close()
        assert bus.is_open

        pump.close()
        assert not bus.is_open
