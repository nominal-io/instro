# instro-daq-labjack

LabJack DAQ drivers for [`instro`](https://github.com/nominal-io/instro).

This package contributes `LabJackTSeriesDriver` under `instro.daq.drivers.labjack`, with model definitions for the T4, T7, and T8. The driver talks to LabJack T-series hardware through the [LabJack LJM](https://support.labjack.com/docs/ljm-software-installer-downloads-t4-t7-t8-digit) library via the `labjack-ljm` Python binding, and runs behind the `InstroDAQ` hardware abstraction alongside the other DAQ vendors.

## Installation

```bash
pip install 'instro[labjack]'
```

The LJM library is a separate vendor install from LabJack and is required at runtime on Windows, macOS, and Linux.

## Usage

Construct the driver with the device serial number, then pass it to `InstroDAQ`:

```python
from instro.daq import InstroDAQ
from instro.daq.drivers.labjack import LabJackTSeriesDriver

daq = InstroDAQ(name="myDAQ", driver=LabJackTSeriesDriver(device_id="440020473"))
```

See the [DAQ guides](https://instro.nominal.io) for channel configuration, hardware timing, and publishing to Nominal Core.

## License

[Apache License 2.0](./LICENSE).
