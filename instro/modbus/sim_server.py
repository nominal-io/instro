"""Simple Modbus TCP server for testing.

Starts a server with holding registers, input registers, coils, and discrete inputs.

Usage:
    python -m instro.modbus.sim_server
"""

import asyncio
import logging
import struct

from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class LoggingDataBlock(ModbusSequentialDataBlock):
    """Data block that logs writes to the console."""

    def __init__(self, label: str, address: int, values: list):
        self._label = label
        super().__init__(address, values)

    def setValues(self, address, values):
        super().setValues(address, values)
        addr = address - 1
        if len(values) == 1:
            log.info(f"  WRITE {self._label} addr={addr} -> {values[0]}")
        else:
            log.info(f"  WRITE {self._label} addr={addr} len={len(values)} -> {values}")


HOST = "127.0.0.1"
PORT = 5020


def pack_float32(value: float) -> tuple[int, int]:
    data = struct.pack(">f", value)
    return int.from_bytes(data[0:2], "big"), int.from_bytes(data[2:4], "big")


def pack_int(fmt: str, value: int | float) -> list[int]:
    data = struct.pack(fmt, value)
    return [int.from_bytes(data[i * 2 : (i + 1) * 2], "big") for i in range(len(data) // 2)]


def create_datastore() -> ModbusServerContext:
    """Create datastore matching ``examples/modbus/simulated_modbus_device.json``."""
    hr = [0] * 4200
    hr[0], hr[1] = pack_float32(72.5)
    hr[2], hr[3] = pack_float32(14.7)
    hr[4096] = 250
    hr[4097] = 4

    ir = [0] * 100
    ir[2], ir[3] = pack_float32(73.1)
    ir[4], ir[5] = pack_float32(72.9)
    ir[16], ir[17] = pack_int(">I", 15500)
    ir[64:68] = pack_int(">Q", 1234567890)

    co = [False] * 10
    co[0] = True
    co[1] = False

    di = [False] * 10
    di[0] = True
    di[1] = False
    di[2] = True

    store = ModbusDeviceContext(
        di=LoggingDataBlock("discrete", 0, [False] + di),
        co=LoggingDataBlock("coil", 0, [False] + co),
        hr=LoggingDataBlock("holding", 0, [0] + hr),
        ir=LoggingDataBlock("input", 0, [0] + ir),
    )

    return ModbusServerContext(devices={1: store}, single=False)


async def run_server():
    context = create_datastore()
    log.info(f"Modbus TCP Sim Server listening on {HOST}:{PORT}")
    await StartAsyncTcpServer(context=context, address=(HOST, PORT))


if __name__ == "__main__":
    asyncio.run(run_server())
