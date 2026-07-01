# ⟢ instro

Python library for talking to test-and-measurement instruments (power supplies, multimeters, electronic loads, DAQs, oscilloscopes, PLCs) from a unified, typed API.

[![PyPI](https://img.shields.io/pypi/v/instro.svg?color=419B55)](https://pypi.org/project/instro/)
[![Downloads](https://img.shields.io/pepy/dt/instro?color=419B55&label=downloads)](https://pypi.org/project/instro/)
[![Docs](https://img.shields.io/badge/docs-instro.nominal.io-419B55)](https://instro.nominal.io)
[![SDK](https://img.shields.io/badge/sdk-nominal--io.github.io-419B55)](https://nominal-io.github.io/instro/)
[![Community](https://img.shields.io/badge/community-community.instro.nominal.io-419B55)](https://community.instro.nominal.io)

## Quickstart

Talk to a simulated PSU. No hardware required.

```bash
# Terminal 1: start the in-process SCPI sim server:
uv run python -m instro.psu.scpi_sim_server
```

```python
# Terminal 2: run this:
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

with InstroPSU(
    name="my-psu",
    driver=SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET"),
    num_channels=2,
) as psu:
    psu.set_voltage(3.3, channel=1)
    psu.output_enable(True, channel=1)
    print(psu.get_voltage(channel=1))
```

That's the whole loop: construct, `open()`, configure, measure, `close()`. When you want to capture the data, attach a publisher to stream it to a file, a custom destination, or [Nominal](https://nominal.io). For the full walkthrough (including the background polling daemon and publishers), see the [official documentation](https://instro.nominal.io).

## Installation

```bash
pip install instro
```

Requires [Python 3.10 to 3.13](https://www.python.org/downloads/).

To work on `instro` itself, clone and install with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/nominal-io/instro.git
cd instro
uv sync --extra all
```

This creates a virtual environment with the core library, all optional vendor drivers, and dev dependencies. Run with `uv run python your_script.py` or activate via `source .venv/bin/activate` (Unix) / `.venv\Scripts\activate` (Windows).

For the full toolchain needed to run `just check` and `just test` (including the native Rust/CMake/LLVM dependencies that `just test` requires), see [Prerequisites](./CONTRIBUTING.md#prerequisites) in the contributing guide.

## Optional Extras

Instro drivers that require a separate vendor sdk installation ship as separate packages so the heavy dependencies stay optional, and community-contributed drivers ship in their own package. Install only what you need:

| Extra | Pulls in <img width="500" height="1"> | Package |
|---|---|---|
| `instro[nidaq]` | NI-DAQmx (Linux + Windows) | [![PyPI](https://img.shields.io/pypi/v/instro-daq-ni.svg?label=instro-daq-ni&color=419B55)](https://pypi.org/project/instro-daq-ni/) |
| `instro[labjack]` | LabJack LJM | [![PyPI](https://img.shields.io/pypi/v/instro-daq-labjack.svg?label=instro-daq-labjack&color=419B55)](https://pypi.org/project/instro-daq-labjack/) |
| `instro[mccdaq]` | MCC UL (Windows-only) | [![PyPI](https://img.shields.io/pypi/v/instro-daq-mcc.svg?label=instro-daq-mcc&color=419B55)](https://pypi.org/project/instro-daq-mcc/) |
| `instro[aardvark]` | Total Phase Aardvark (I2C) | [![PyPI](https://img.shields.io/pypi/v/instro-i2c-aardvark.svg?label=instro-i2c-aardvark&color=419B55)](https://pypi.org/project/instro-i2c-aardvark/) |
| `instro[ethernetip]` | EtherNet/IP support for Allen-Bradley PLCs | [![PyPI](https://img.shields.io/pypi/v/instro-ethernetip.svg?label=instro-ethernetip&color=419B55)](https://pypi.org/project/instro-ethernetip/) |
| `instro[contrib]` | Community-contributed hardware drivers | [![PyPI](https://img.shields.io/pypi/v/instro-contrib.svg?label=instro-contrib&color=419B55)](https://pypi.org/project/instro-contrib/) |
| `instro[unstable]` | In-development features whose API isn't settled | [![PyPI](https://img.shields.io/pypi/v/instro-unstable.svg?label=instro-unstable&color=419B55)](https://pypi.org/project/instro-unstable/) |
| `instro[all]` | Everything above | — |

Pass the extra package name in brackets to `pip install`:

```bash
pip install "instro[labjack]"
pip install "instro[nidaq,contrib]"
```

## Supported devices

| Category | Class | Vendors |
|---|---|---|
| Power Supply | `InstroPSU` | B&K Precision (9115, 914X), Keysight (E36100-series), Rigol (DP800-series), Siglent (SPD3303), TDK Lambda (Genesys), simulated |
| Multimeter | `InstroDMM` | Agilent 34401A, Keithley 2400, Keithley 2750 (unstable) |
| Electronic Load | `InstroELoad` | B&K Precision (85xxB-series) |
| Oscilloscope | `InstroScope` | Keysight (1200X-series), Tektronix (2-series), Siglent (SDS1000X-E) |
| DAQ | `InstroDAQ` | Keysight 34980A, NI-DAQmx, LabJack T-series, MCC USB-series |
| I2C | `I2CInterface` | Total Phase Aardvark |
| Modbus | `ModbusDevice` | Any Modbus TCP / RTU device |
| EtherNet/IP | `EtherNetIPDevice` | Allen-Bradley / CompactLogix-class PLCs |

Don't see your vendor? Drivers the maintainers can't test directly land in [`instro-contrib`](./packages/instro-contrib/).
Install them with `instro[contrib]`. See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the verification expectations.

## Contributing

- **Humans**: see [`CONTRIBUTING.md`](./CONTRIBUTING.md) for development setup, PR conventions, and where different kinds of contributions belong in the workspace.
- **AI coding tools** (Claude Code, Cursor, Codex, Copilot Workspace, …): see [`AGENTS.md`](./AGENTS.md) for codebase landmarks, conventions, and common workflows. The repo ships reusable skills and subagents for both Claude Code (`.claude/`) and Codex CLI (`.agents/`, `.codex/`). The existing skills are `add-instrument-driver` which scaffolds a new vendor driver from a programming manual/API, and `validate-driver-hardware` which smoke-tests an authored driver against the real instrument and self-corrects it. See [Repo skills and subagents](./AGENTS.md#repo-skills-and-subagents).

## License

[Apache License 2.0](./LICENSE). Third-party dependency notices and proprietary vendor runtime requirements are documented in [NOTICE](./NOTICE).
