# instro-contrib

Community-contributed drivers for [`instro`](https://github.com/nominal-io/instro).

This package hosts drivers for devices the `instro` maintainers don't own and can't verify directly. Hardware verification is done by the contributor, so the support bar is lower than core's — but every driver still passes type checking, lint, and a mocked-transport unit test under review. The `contrib` namespace travels with every import as the disclaimer.

## Installation

```bash
pip install 'instro[contrib]'   # with the instro core
pip install instro-contrib       # standalone
```

## Usage

Contrib drivers mirror the core `instro` layout with `contrib` inserted after the top-level package name, and run behind the same category HALs (`InstroPSU`, `InstroDMM`, …):

```python
from instro.psu import InstroPSU
from instro.contrib.psu.drivers import SiglentSPDxxx

psu = InstroPSU(name="bench", driver=SiglentSPDxxx("TCPIP::192.168.1.10::INSTR"), num_channels=1)
```

API stability is not guaranteed release-to-release; pin to a specific version if you need reproducibility.

## Contributing

New drivers are welcome. See [CONTRIBUTING.md](https://github.com/nominal-io/instro/blob/main/CONTRIBUTING.md#instro-contrib-community-contributed-drivers) for the contribution bar and layout.

## License

[Apache License 2.0](./LICENSE).
