from instro.daq import InstroDAQ
from instro.daq.drivers.ni import NIDAQDriver
from instro.daq.types import DigitalPortWidth, Direction, Logic

daq = InstroDAQ(name="daq", driver=NIDAQDriver(device_id="cDAQ1"))
with daq:
    daq.configure_digital_port(Direction.INPUT, "cDAQ1Mod3/port0", Logic.LOW, DigitalPortWidth.WIDTH_16, alias="bus")
    print(daq.read_digital_port("bus").channel_data)
    # -> {'daq.bus': [65535.0]}  # 0xFFFF from an 8-line port (raw read was 0, XORed with the 16-bit mask)

    daq.configure_digital_port(Direction.OUTPUT, "cDAQ1Mod3/port0", Logic.HIGH, DigitalPortWidth.WIDTH_16, alias="obus")
    daq.write_digital_port("obus", 300)  # exceeds the 8 physical lines; accepted, silently masked
