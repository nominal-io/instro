<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/nominal-io/instro/main/res/logo/instro-logo-ascii-white.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/nominal-io/instro/main/res/logo/instro-logo-ascii-black.svg">
  <img width="512" alt="Nominal Instro SDK Logo" src="https://raw.githubusercontent.com/nominal-io/instro/main/res/logo/instro-logo-ascii-black.svg">
</picture>
</div>

<br>

An open-source, vendor-agnostic Python library for interfacing with test equipment.

[![PyPI](https://img.shields.io/pypi/v/instro.svg?color=419B55)](https://pypi.org/project/instro/)
[![Downloads](https://img.shields.io/pepy/dt/instro?color=419B55&label=downloads)](https://pypi.org/project/instro/)
[![Docs](https://img.shields.io/badge/docs-instro.nominal.io-419B55)](https://instro.nominal.io)
[![SDK](https://img.shields.io/badge/sdk-nominal--io.github.io-419B55)](https://nominal-io.github.io/instro/)
[![Community](https://img.shields.io/badge/community-community.instro.nominal.io-419B55)](https://community.instro.nominal.io)
[![Discord](https://img.shields.io/badge/discord-join-419B55?logo=discord&logoColor=white)](https://discord.gg/nN4RzhQkr)

## Quickstart

```python
from instro.daq.drivers.labjack import LabJackTSeriesDriver
# from instro.daq.drivers.ni import NIDAQDriver
from instro.daq import InstroDAQ
from instro.daq.types import Direction
from instro.lib.publishers import FilePublisher

pub = FilePublisher(format="jsonl", directory="/tmp/instro/")

daq = InstroDAQ(
  name       = "myDAQ",
  driver     = LabJackTSeriesDriver(device_id="1234"),
  publishers = [pub],
  #driver    = NIDAQDriver(device_id="Dev1") # swap drivers, same code
  )
daq.open()
daq.configure_analog_channel(
    direction=Direction.INPUT, physical_channel="AIN0", alias="ch_0", range_min=0, range_max=5
)
measurement = daq.read_analog()  # written to `publishers`
print(pub.file_path.read_text())
```

## [Installation](https://instro.nominal.io/instrumentation/installation)

### Basic
<table>
<tr>
<td>

```bash
uv add instro
```

</td>
<td>

```bash
pip install instro
```

</td>
</tr>
</table>

### With Additional Packages
<table>
<tr>
<td>

```bash
uv add "instro[all]"
```

</td>
<td>

```bash
pip install "instro[all]"
```

</td>
</tr>
</table>

Available packages:

| Instrument Type | Package | Contents |
| --- | --- | --- |
| **All** | `all` | All drivers in this table|
| **DAQ** | `daq` | All DAQ drivers |
|  | `nidaq` | NI DAQ package |
|  | `mccdaq` | Measurement Computing (MCC) package |
|  | `labjack` | LabJack package |
| **I2C** | `i2c` | All I2C packages |
|  | `aardvark` | TotalPhase Aardvark package |
| **EtherNet** | `ethernetip` | EtherNet/IP support |
| **Other** | `contrib` | Community-contributed drivers |
|  | `unstable` | Experimental unstable modules |

See [Installation](https://instro.nominal.io/instrumentation/installation) for more info.

## [Supported devices](https://instro.nominal.io/instrumentation/supported-instruments)

<!-- --8<-- [start:supported-devices] -->
| Category | Class | Vendors |
|---|---|---|
| Power Supply | `InstroPSU` | B&K Precision (9115, 914X), EA Elektro-Automatik (PSB 10000-series), Keysight (E36100-series), Rigol (DP800-series), Siglent (SPD3303), TDK Lambda (Genesys), simulated |
| Multimeter | `InstroDMM` | Agilent 34401A, Keysight 34461A, Keithley 2400, Keithley 2750 (unstable), simulated |
| Arbitrary Waveform Generator | `InstroAWG` | Keysight (33521B), Rigol (DG1022Z) |
| Electronic Load | `InstroELoad` | B&K Precision (85xxB-series), EA Elektro-Automatik (PSB 10000-series) |
| Oscilloscope | `InstroScope` | Keysight (1200X-series), Tektronix (2-series), Siglent (SDS1000X-E) |
| Flow Controller | `InstroFlowController` | Alicat MC-series |
| DAQ | `InstroDAQ` | Keysight 34980A, NI-DAQmx, LabJack T-series, MCC USB-series, DewesoftX (unstable) |
| I2C | `I2CInterface` | Total Phase Aardvark |
| Modbus | `ModbusDevice` | Any Modbus TCP / RTU device |
| Motor Controller | `InstroMotorController` | VESC 6 over CAN via python-can (unstable) |
| EtherNet/IP | `EtherNetIPDevice` | Allen-Bradley / CompactLogix-class PLCs |
<!-- --8<-- [end:supported-devices] -->

See [Supported devices](https://instro.nominal.io/instrumentation/supported-instruments) for more info.

## [Contributing](https://github.com/nominal-io/instro/blob/main/CONTRIBUTING.md)

To work on `instro` itself, clone and install with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/nominal-io/instro.git
cd instro
uv sync
```
See [Contributing](https://github.com/nominal-io/instro/blob/main/CONTRIBUTING.md) for more info.

## License

[Apache License 2.0](https://github.com/nominal-io/instro/blob/main/LICENSE). Third-party dependency notices and proprietary vendor runtime requirements are documented in [NOTICE](https://github.com/nominal-io/instro/blob/main/NOTICE).
