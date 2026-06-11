# instro-daq-ni

National Instruments DAQ drivers for [`instro`](https://github.com/nominal-io/instro).

This package contributes `NIDAQDriver` under `instro.daq.drivers.ni`. The driver talks to NI multifunction DAQ hardware through the [NI-DAQmx](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html) runtime via the `nidaqmx` Python binding, and runs behind the `InstroDAQ` hardware abstraction alongside the other DAQ vendors.

## Installation

```bash
pip install 'instro[nidaq]'
```

The NI-DAQmx runtime is a separate vendor install from National Instruments and is required at runtime on Linux and Windows.

## Usage

Construct the driver with the device name as it appears in NI MAX, then pass it to `InstroDAQ`:

```python
from instro.daq import InstroDAQ
from instro.daq.drivers.ni import NIDAQDriver

daq = InstroDAQ(name="myDAQ", driver=NIDAQDriver(device_id="Dev1"))
```

See the [DAQ guides](https://instro.nominal.io) for channel configuration, hardware timing, and publishing to Nominal Core.

## License

[Apache License 2.0](./LICENSE).
