"""Tests for CANInterface against python-can's in-process virtual bus (issue #641)."""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import can
import cantools
import pytest

from instro.unstable.canbus import CANInterface
from instro.unstable.transports import CanConfig, CanTransport

DBC = Path(__file__).parent / "vehicle.dbc"


@pytest.fixture
def channel() -> str:
    """A virtual-bus channel name no other test shares."""
    return f"test-{uuid4()}"


@pytest.fixture
def peer(channel: str) -> Iterator[can.BusABC]:
    """The other node on the bus: sends frames to the interface and receives what it writes."""
    bus = can.Bus(interface="virtual", channel=channel)
    yield bus
    bus.shutdown()


def _interface(connection: str | CanTransport, **overrides: object) -> CANInterface:
    config = {"device": {"name": "vehicle_bus"}, "dbc": DBC, **overrides}
    if isinstance(connection, CanTransport):
        return CANInterface(config, connection=connection)
    return CANInterface(config, connection={"interface": "virtual", "channel": connection})


@pytest.fixture
def interface(channel: str) -> Iterator[CANInterface]:
    """An open interface receiving every DBC message."""
    bus = _interface(channel)
    bus.open()
    yield bus
    bus.close()


def _bms_status(volts_raw: bytes, amps_raw: bytes) -> can.Message:
    return can.Message(arbitration_id=0x123, data=volts_raw + amps_raw + bytes(4), is_extended_id=False)


def test_interfaces_sharing_a_transport_keep_the_bus_open_until_the_last_closes(channel: str) -> None:
    transport = CanTransport(CanConfig(channel=channel, interface="virtual"))
    first, second = _interface(transport), _interface(transport)

    first.open()
    second.open()
    first.close()
    assert transport.is_open

    second.close()
    assert not transport.is_open


def test_read_decodes_the_newest_frame_with_dbc_scaling(interface: CANInterface, peer: can.BusABC) -> None:
    peer.send(_bms_status(volts_raw=(10_000).to_bytes(2, "little"), amps_raw=bytes(2)))
    # pack_voltage: raw 40000 * 0.01 V; pack_current: raw -125 * 0.1 A
    peer.send(_bms_status(volts_raw=(40_000).to_bytes(2, "little"), amps_raw=(-125).to_bytes(2, "little", signed=True)))

    voltage = interface.read("BMS_Status.pack_voltage")
    current = interface.read("BMS_Status.pack_current")

    assert voltage.channel_data == {"vehicle_bus.BMS_Status.pack_voltage": [400.0]}
    assert current.latest == -12.5


def test_frames_match_on_id_and_id_type(interface: CANInterface, peer: can.BusABC) -> None:
    peer.send(can.Message(arbitration_id=0x123, data=bytes(8), is_extended_id=True))  # not BMS_Status: wrong id type
    peer.send(can.Message(arbitration_id=0x18FF50E5, data=(-1500).to_bytes(2, "little", signed=True) + bytes(6)))

    assert interface.read("Motor_Feedback.speed").latest == -1500
    with pytest.raises(RuntimeError, match="No 'BMS_Status' frame received"):
        interface.read("BMS_Status.pack_voltage")


def test_read_rejects_messages_outside_rx_messages(channel: str) -> None:
    bus = _interface(channel, rx_messages=["BMS_Status"])
    bus.open()
    try:
        with pytest.raises(KeyError, match="'Motor_Feedback' is not in rx_messages"):
            bus.read("Motor_Feedback.speed")
    finally:
        bus.close()


def test_poll_publishes_each_message_with_a_new_frame_once(interface: CANInterface, peer: can.BusABC) -> None:
    peer.send(_bms_status(volts_raw=(40_000).to_bytes(2, "little"), amps_raw=bytes(2)))

    [measurement] = interface.poll()

    assert measurement.channel_data == {
        "vehicle_bus.BMS_Status.pack_voltage": [400.0],
        "vehicle_bus.BMS_Status.pack_current": [0.0],
    }
    assert interface.poll() == []  # nothing new on the bus


@pytest.fixture
def tx_interface(channel: str) -> Iterator[CANInterface]:
    """An open interface allowed to send Motor_Command."""
    bus = _interface(channel, tx_messages=["Motor_Command"])
    bus.open()
    yield bus
    bus.close()


def test_write_sends_the_whole_frame_keeping_earlier_signal_values(
    tx_interface: CANInterface, peer: can.BusABC
) -> None:
    tx_interface.write("Motor_Command.torque_request", 12.5)
    command = tx_interface.write("Motor_Command.enable", 1)

    peer.recv(timeout=1.0)
    frame = peer.recv(timeout=1.0)
    assert frame is not None
    assert (frame.arbitration_id, frame.is_extended_id) == (0x200, False)
    # torque_request kept at raw 125 (12.5 Nm / 0.1) while enable is set
    assert bytes(frame.data[:3]) == bytes([125, 0, 1])
    assert command.channel_data == {"vehicle_bus.Motor_Command.enable.cmd": 1}


def test_write_outside_the_dbc_range_sends_nothing(tx_interface: CANInterface, peer: can.BusABC) -> None:
    with pytest.raises(cantools.database.EncodeError, match="torque_request"):
        tx_interface.write("Motor_Command.torque_request", 999)

    assert peer.recv(timeout=0.05) is None


def test_frames_without_decodable_signal_data_are_skipped(interface: CANInterface, peer: can.BusABC) -> None:
    peer.send(_bms_status(volts_raw=(40_000).to_bytes(2, "little"), amps_raw=bytes(2)))
    peer.send(can.Message(arbitration_id=0x123, is_extended_id=False, is_remote_frame=True, dlc=8))
    peer.send(can.Message(arbitration_id=0x123, is_extended_id=False, data=b"\x01\x02"))  # DLC 2, DBC says 8
    peer.send(can.Message(arbitration_id=0x18FF50E5, data=(-1500).to_bytes(2, "little", signed=True) + bytes(6)))

    published = {name: values for m in interface.poll() for name, values in m.channel_data.items()}

    assert published["vehicle_bus.BMS_Status.pack_voltage"] == [400.0]
    assert published["vehicle_bus.Motor_Feedback.speed"] == [-1500]


def test_send_frame_sends_raw_bytes_outside_the_dbc(interface: CANInterface, peer: can.BusABC) -> None:
    interface.send_frame(0x7DF, b"\x02\x01\x0d", is_extended_id=False)

    frame = peer.recv(timeout=1.0)
    assert frame is not None
    assert (frame.arbitration_id, frame.is_extended_id, bytes(frame.data)) == (0x7DF, False, b"\x02\x01\x0d")
