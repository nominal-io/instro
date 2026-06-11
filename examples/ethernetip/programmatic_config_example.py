"""Example: Building an EtherNet/IP config programmatically (no JSON file).

Constructs an EtherNetIPConfig in Python and passes it directly to EtherNetIPDevice.
Useful when configs are generated dynamically or assembled from multiple sources.

Configure the PLC endpoint with environment variables:
    export ETHERNETIP_HOST=192.168.1.10
    export ETHERNETIP_PORT=44818
    export ETHERNETIP_SLOT=0

Writes are disabled by default. To also run the write section:
    export ETHERNETIP_ENABLE_WRITES=1

Then run:
    python examples/ethernetip/programmatic_config_example.py
"""

import os
import time

from instro.lib.types import DeviceInfo
from instro.unstable.ethernetip import (
    EtherNetIPBackplaneHop,
    EtherNetIPConfig,
    EtherNetIPConnectionInfo,
    EtherNetIPDevice,
    EtherNetIPRoutePath,
    TagDef,
    TimingConfig,
)


def connection_from_env() -> EtherNetIPConnectionInfo:
    route_path = None
    if slot := os.environ.get("ETHERNETIP_SLOT"):
        route_path = EtherNetIPRoutePath(hops=[EtherNetIPBackplaneHop(type="backplane", slot=int(slot))])

    return EtherNetIPConnectionInfo(
        host=os.environ.get("ETHERNETIP_HOST", "192.168.1.10"),
        port=int(os.environ.get("ETHERNETIP_PORT", "44818")),
        route_path=route_path,
    )


connection = connection_from_env()
timing = TimingConfig(poll_interval=0.5)

config = EtherNetIPConfig(
    device=DeviceInfo(
        name="cell_controller",
        description="Programmatically configured EtherNet/IP PLC",
        manufacturer="Rockwell Automation",
        model="ControlLogix / CompactLogix",
    ),
    connection=connection,
    timing=timing,
    tags=[
        TagDef(
            alias="line_speed",
            tag_name="LineSpeedActual",
            data_type="real",
        ),
        TagDef(
            alias="speed_setpoint",
            tag_name="LineSpeedSetpoint",
            data_type="real",
            write_min=0.0,
            write_max=500.0,
        ),
        TagDef(
            alias="pressure_psi",
            tag_name="PressurePsi",
            data_type="real",
        ),
        TagDef(
            alias="running",
            tag_name="Running",
            data_type="bool",
        ),
        TagDef(
            alias="mode",
            tag_name="OperatingMode",
            data_type="dint",
            write_min=0,
            write_max=3,
        ),
        TagDef(
            alias="run_command",
            tag_name="RunCommand",
            data_type="bool",
        ),
    ],
)


def main() -> None:
    device = EtherNetIPDevice(config, autostart=True)
    enable_writes = os.environ.get("ETHERNETIP_ENABLE_WRITES") == "1"

    try:
        print(f"Connected to {config.device.name} at {connection.address}")
        print(f"Polling {len(config.tags)} configured tags every {timing.poll_interval}s\n")

        print(f"line_speed:   {device.read_tag('line_speed').latest}")
        print(f"pressure_psi: {device.read_tag('pressure_psi').latest}")
        print(f"running:      {device.read_tag('running').latest}")

        time.sleep(2)
        buffered = device.get_channel("cell_controller.line_speed", length=2)
        print(f"\nline_speed buffered samples: {buffered.channel_data['cell_controller.line_speed']}")

        if enable_writes:
            device.write_tag("mode", 2)
            print(f"\nWrote mode=2 -> read back: {device.read_tag('mode').latest}")

            device.write_tag("speed_setpoint", 100.0)
            print(f"Wrote speed_setpoint=100.0 -> read back: {device.read_tag('speed_setpoint').latest}")

            device.write_tag("run_command", True)
            print("Wrote run_command=True")
        else:
            print("\nWrites disabled; set ETHERNETIP_ENABLE_WRITES=1 to run write examples.")

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        device.close()


if __name__ == "__main__":
    main()
