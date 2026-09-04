# instro-unstable

In-development categories and abstractions for [`instro`](https://github.com/nominal-io/instro) whose API isn't settled.

Modules live here while their HAL surface is still changing. Once an API stabilizes, the module graduates into the core `instro` package under the same category name and the `unstable` segment drops out of the import path (`InstroAWG` moved this way in `instro` 1.18.0).

## Installation

```bash
pip install 'instro[unstable]'   # with the instro core
pip install instro-unstable       # standalone
```

## Usage

Unstable modules mirror the core `instro` layout with `unstable` inserted after the top-level package name, and run behind the same `Instrument` base as the core HALs:

```python
from instro.unstable.vna import InstroVNA
from instro.unstable.vna.drivers import NanoVNAv2Clone

vna = InstroVNA(name="bench", driver=NanoVNAv2Clone(port="COM3"))
```

Currently shipping: `InstroVNA`, `InstroMotorController`, `InstroFlowController`, the `EspecGL` environmental chamber, a Keithley 2750 `InstroDMM` driver, and the `CanTransport` transport.

API stability is not guaranteed release-to-release; pin to a specific version if you need reproducibility.

## License

[Apache License 2.0](./LICENSE).
