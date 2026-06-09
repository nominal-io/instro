# instro-daq-mcc

Measurement Computing (MCC) DAQ drivers for [`instro`](https://github.com/nominal-io/instro).

This package contributes `MCCDriver` under `instro.daq.drivers.mcc`. The driver talks to MCC USB-series hardware through the [MCC Universal Library](https://www.mccdaq.com/Software-Downloads) via the `mcculw` Python binding, and runs behind the `InstroDAQ` hardware abstraction alongside the other DAQ vendors.

## Installation

```bash
pip install 'instro[mccdaq]'
```

The MCC Universal Library (`mcculw`) is Windows-only, so this package runs on Windows.

## Usage

Construct the driver with the MCC device ID, optionally suffixed with `:<board_number>` (default 0), then pass it to `InstroDAQ`:

```python
from instro.daq import InstroDAQ
from instro.daq.drivers.mcc import MCCDriver

daq = InstroDAQ(name="myDAQ", driver=MCCDriver(device_id="344371:0"))
```

See the [DAQ guides](https://instro.nominal.io) for channel configuration, hardware timing, and publishing to Nominal Core.

## License

[Apache License 2.0](./LICENSE).
