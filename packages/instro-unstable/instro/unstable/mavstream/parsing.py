"""MAVLink parsing functions, copied unchanged from existing mavstream library.

Do not edit the function bodies here. Change them upstream first, then copy them again.
"""

from typing import ClassVar, Iterable, Protocol

from pymavlink import mavutil


class MAVLinkMessageProtocol(Protocol):
    _timestamp: float
    msgname: ClassVar[str]
    ordered_fieldnames: ClassVar[list[str]]
    fieldtypes: ClassVar[list[str]]
    array_lengths: ClassVar[list[int]]
    instance_field: ClassVar[str | None]


def get_channel_values(message: MAVLinkMessageProtocol) -> Iterable[tuple[str, int, float | str]]:
    """Yield (channel_name, timestamp_nanos, value) tuples for a MAVLink message.

    The channel name is in the format `<message_type>.<field_name>`.
    If the message has an instance field, the channel name will be suffixed with `[<instance>]`.
    For e.g. `RAW_IMU[0].xacc`

    Args:
        message: MAVLink message to extract channel values from

    Yields:
        Tuples of (channel_name, timestamp_nanoseconds, value)
    """
    mtype = type(message)
    # Convert float timestamp (seconds) to integer nanoseconds for Nominal's write stream
    timestamp_nanos = int(message._timestamp * 1e9)  # type: ignore[attr-defined]

    for fname, ftype, flength in zip(mtype.ordered_fieldnames, mtype.fieldtypes, mtype.array_lengths):
        # Nominal currently does not support arrays; only floats and strings
        if flength > 0 and ftype != "char":
            continue

        channel_name = f"{mtype.msgname}.{fname}"
        if mtype.instance_field is not None:
            instance = getattr(message, mtype.instance_field)
            channel_name += f"[{instance}]"

        if ftype == "char":
            value = str(getattr(message, fname))
        else:
            value = float(getattr(message, fname))  # type: ignore[assignment]

        yield channel_name, timestamp_nanos, value


def read_messages(conn: mavutil.mavfile, blocking: bool = False) -> Iterable[MAVLinkMessageProtocol]:  # noqa: D417
    """Read messages from a MAVLink connection.

    Args:
        conn: MAVLink file/connection to read from

    Yields:
        MAVLink messages from the connection
    """
    while (msg := conn.recv_match(blocking=blocking)) is not None:
        yield msg
