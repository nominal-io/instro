"""Feature test: two ModbusDevices share one transport, each with its own unit address (GH #262).

Encodes the primary user story end-to-end: construct one ModbusTCPTransport, pass it to several
devices each with its own ``unit_id``, and have each read and write only its own unit over
the one session — surviving the first device's close and released by the last. Also pins the shape
the addresses live in: no unit_id on a transport, ``unit_id`` on the device (from the constructor
kwarg or the config's ``connection.unit_id``), and no ModbusDriver/TCPConnection/RTUConnection. A second, narrow scenario proves the same one-session
invariant on the serial path with a mocked pymodbus client, since CI has no RS-485 line.
Fails until the transport classes exist.
"""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest
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


def test_two_devices_share_one_connection(two_unit_server):
    for retired in ("ModbusDriver", "TCPConnection", "RTUConnection", "ConnectionType", "ModbusUnit"):
        assert not hasattr(modbus_transport, retired)
    for retired in ("TCPConnection", "RTUConnection"):
        assert not hasattr(modbus_package, retired)

    with pytest.raises(TypeError):
        ModbusTCPTransport(host=HOST, port=TEST_PORT, unit_id=5)

    shared_bus = ModbusTCPTransport(host=HOST, port=TEST_PORT)
    assert not hasattr(shared_bus, "unit_id")

    # A shared transport with no address is an error, never a silent unit 1.
    with pytest.raises(ValueError, match="unit_id"):
        ModbusDevice(config=DEVICE_CONFIG, connection=shared_bus, name="unaddressed")

    # Disagreeing addresses from two sources are a named error, never a silent pick.
    with pytest.raises(ValueError, match="unit"):
        ModbusDevice(
            config=DEVICE_CONFIG,
            connection={"transport": "tcp", "host": HOST, "port": TEST_PORT, "unit_id": FLOW_UNIT},
            name="doubly_addressed",
            unit_id=PUMP_UNIT,
        )

    flow_meter = ModbusDevice(config=DEVICE_CONFIG, connection=shared_bus, unit_id=FLOW_UNIT, name="flow_meter")
    pump = ModbusDevice(config=DEVICE_CONFIG, connection=shared_bus, unit_id=PUMP_UNIT, name="pump")

    flow_meter.open()
    pump.open()

    assert shared_bus.is_open
    assert (flow_meter.unit_id, pump.unit_id) == (FLOW_UNIT, PUMP_UNIT)

    # Required per call on the transport, on an open session: omission is a TypeError, never unit 1.
    with pytest.raises(TypeError):
        shared_bus.read_holding_registers(SETPOINT_ADDRESS, 1)
    with pytest.raises(TypeError):
        shared_bus.read_typed("holding", SETPOINT_ADDRESS, "uint16")

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

    # A device that builds its own private transport may take its address from the constructor.
    solo = ModbusDevice(
        config=DEVICE_CONFIG,
        connection={"transport": "tcp", "host": HOST, "port": TEST_PORT},
        name="solo",
        unit_id=FLOW_UNIT,
    )
    solo.open()
    assert solo.read("setpoint").latest == FLOW_VALUE
    solo.close()


def test_connection_block_unit_id_addresses_the_device(two_unit_server):
    # Regression gate for existing configs: connection.unit_id is the canonical config
    # location for the address and must keep loading and routing, not raise.
    legacy = ModbusDevice(
        config=DEVICE_CONFIG,
        connection={"transport": "tcp", "host": HOST, "port": TEST_PORT, "unit_id": PUMP_UNIT},
        name="legacy",
    )
    assert legacy.unit_id == PUMP_UNIT
    legacy.open()
    legacy.write("setpoint", 3333)
    assert legacy.read("setpoint").latest == 3333
    legacy.close()

    config_with_nested = {
        **DEVICE_CONFIG,
        "connection": {"transport": "tcp", "host": HOST, "port": TEST_PORT, "unit_id": FLOW_UNIT},
    }
    from_config = ModbusDevice(config=config_with_nested, name="legacy_config")
    assert from_config.unit_id == FLOW_UNIT
    from_config.open()
    # The nested address routes: this device sees the flow unit, not the 3333 just written at pump.
    assert from_config.read("setpoint").latest != 3333
    from_config.close()


def test_unit_id_conflict_between_constructor_and_config_is_rejected(two_unit_server):
    config_with_address = {
        **DEVICE_CONFIG,
        "connection": {"transport": "tcp", "host": HOST, "port": TEST_PORT, "unit_id": FLOW_UNIT},
    }

    # Constructor kwarg disagrees with the config's connection block: a named error, not a
    # silent override.
    with pytest.raises(ValueError, match="unit_id"):
        ModbusDevice(config=config_with_address, unit_id=PUMP_UNIT)

    # Agreeing values are not a conflict.
    device = ModbusDevice(config=config_with_address, unit_id=FLOW_UNIT)
    assert device.unit_id == FLOW_UNIT


def test_unit_id_is_read_only(two_unit_server):
    # No public setter at all: the address is bound once at construction (constructor kwarg
    # or config) and never reassignable afterward, open or closed. Reassigning it, even to
    # redirect a live/polling connection, is not an API this class offers, rather than an
    # error path within one.
    shared_bus = ModbusTCPTransport(host=HOST, port=TEST_PORT)
    device = ModbusDevice(config=DEVICE_CONFIG, connection=shared_bus, unit_id=FLOW_UNIT, name="reassign_check")

    with pytest.raises(AttributeError):
        device.unit_id = PUMP_UNIT
    assert device.unit_id == FLOW_UNIT


def test_two_devices_share_one_serial_port():
    """The RS-485 half: one ModbusRTUTransport, two devices, exactly one serial client constructed."""
    # Patched at the module attribute, not at pymodbus.client: the transport imports the client
    # class at module scope, so instro's own binding is what has to be replaced. Same form as
    # tests/lib/test_visa_driver.py's patch of instro.lib.transports.visa.pyvisa.ResourceManager.
    with patch("instro.lib.transports.modbus.ModbusSerialClient") as serial_client:
        serial_client.return_value.connect.return_value = True

        bus = ModbusRTUTransport(port="/dev/ttyUSB0", baudrate=19200)
        flow = ModbusDevice(config=DEVICE_CONFIG, connection=bus, unit_id=FLOW_UNIT, name="flow_meter")
        pump = ModbusDevice(config=DEVICE_CONFIG, connection=bus, unit_id=PUMP_UNIT, name="pump")

        flow.open()
        pump.open()

        # pymodbus opens serial exclusive=True, so a second construction is the bug this fixes.
        assert serial_client.call_count == 1
        assert bus.is_open

        flow.close()
        assert bus.is_open

        pump.close()
        assert not bus.is_open
