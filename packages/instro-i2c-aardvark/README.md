# instro-i2c-aardvark

Total Phase Aardvark I2C driver for [`instro`](https://github.com/nominal-io/instro).

This package contributes `Aardvark` under `instro.i2c.drivers.totalphase`. The driver talks to the [Total Phase Aardvark](https://www.totalphase.com/products/aardvark-i2cspi/) I2C/SPI host adapter through the `pyaardvark` binding, and runs behind the `I2CInterface` hardware abstraction.

## Installation

```bash
pip install 'instro[aardvark]'   # alias: instro[i2c]
```

The Aardvark shared library is a separate vendor install from Total Phase and is required at runtime on Windows, macOS, and Linux.

## Usage

Construct the driver with the adapter serial number, then pass it to `I2CInterface` along with a system definition describing the peripherals on the bus:

```python
from instro.i2c import I2CInterface
from instro.i2c.drivers.totalphase import Aardvark

i2c = I2CInterface(
    name="myI2C",
    driver=Aardvark(serial_number="2239-764425"),
    system_definition=my_system_definition(),
)
```

See the [I2C guides](https://instro.nominal.io) for system definitions, register and field access, and publishing to Nominal Core.

## License

[Apache License 2.0](./LICENSE).
