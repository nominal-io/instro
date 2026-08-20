"""Configure one SE and one DIFF voltage channel, then compare the driver's record to the board's state."""

from mcculw import ul
from mcculw.enums import AnalogInputMode, BoardInfo, InfoType, InterfaceType
from mcculw.ul import ULError

from instro.daq.drivers.mcc import MCCDriver
from instro.daq.types import AnalogVoltageChannel, Direction, TerminalConfig

BOARD_NUM = 0
DEVICE_MATCH = ""  # substring of the product name to probe; "" takes the first device
CHANNELS = (("0", TerminalConfig.RSE), ("1", TerminalConfig.DIFF))


def get(item: BoardInfo, dev_num: int = 0) -> int | str:
    try:
        return ul.get_config(InfoType.BOARDINFO, BOARD_NUM, dev_num, item)
    except ULError as e:
        return f"<err {e.errorcode}>"


def mode_name(value: int | str) -> str:
    if isinstance(value, str):
        return value
    return f"{AnalogInputMode(value).name}({value})" if value in list(AnalogInputMode) else f"UNKNOWN({value})"


def hardware(label: str) -> None:
    per_chan = "  ".join(f"ch{p}={mode_name(get(BoardInfo.ADCHANAIMODE, int(p)))}" for p, _ in CHANNELS)
    print(f"{label:<24} NUMADCHANS={get(BoardInfo.NUMADCHANS)}  board={mode_name(get(BoardInfo.ADAIMODE))}  {per_chan}")


def channel(physical: str, terminal_config: TerminalConfig) -> AnalogVoltageChannel:
    return AnalogVoltageChannel(
        physical_channel=physical,
        alias=f"ch{physical}_{terminal_config.name}",
        direction=Direction.INPUT,
        range_max=10.0,
        range_min=-10.0,
        scaler=None,
        terminal_config=terminal_config,
    )


ul.ignore_instacal()
inventory = ul.get_daq_device_inventory(InterfaceType.ANY)
devices = [dev for dev in inventory if DEVICE_MATCH in dev.product_name]
if not devices:
    raise SystemExit(f"No device matching '{DEVICE_MATCH}'. Detected: {[d.product_name for d in inventory]}")

device = devices[0]
print(f"device: {device.product_name} ({device.unique_id})\n")

driver = MCCDriver(f"{device.unique_id}:{BOARD_NUM}")
driver.open()
try:
    hardware("at open:")
    for physical, terminal_config in CHANNELS:
        try:
            driver.configure_ai_voltage_channel(channel(physical, terminal_config))
            hardware(f"after ch{physical} {terminal_config.name}:")
        except (ValueError, ULError) as e:
            print(f"configure ch{physical} {terminal_config.name} failed: {type(e).__name__}: {e}")

    print("\ndriver state:")
    for alias, configured in driver.ai_channels.items():
        print(f"  {alias:<12} physical={configured.physical_channel:<3} {getattr(configured, 'terminal_config', None)}")

    print("\nhardware state:")
    print(f"  board mode = {mode_name(get(BoardInfo.ADAIMODE))}")
    for physical, _ in CHANNELS:
        print(f"  ch{physical} mode   = {mode_name(get(BoardInfo.ADCHANAIMODE, int(physical)))}")
    print(f"  NUMADCHANS = {get(BoardInfo.NUMADCHANS)}")
finally:
    driver.close()
