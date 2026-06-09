# ⟢ instro

Python library for talking to test-and-measurement instruments (power supplies, multimeters, electronic loads, DAQs, oscilloscopes, PLCs) from a unified, typed API.

[![PyPI](https://img.shields.io/pypi/v/instro.svg)](https://pypi.org/project/instro/)

## Installation

```bash
pip install instro
```

Requires [Python 3.10+](https://www.python.org/downloads/).

To work on `instro` itself, clone and install with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/nominal-io/instro.git
cd instro
uv sync --extra all
```

This creates a virtual environment with the core library, all optional vendor drivers, and dev dependencies. Run with `uv run python your_script.py` or activate via `source .venv/bin/activate` (Unix) / `.venv\Scripts\activate` (Windows).

### Optional vendor extras

Native-SDK drivers ship as separate workspace packages so the heavy dependencies stay optional. Install only what you need:

| Extra | Pulls in |
|---|---|
| `instro[nidaq]` | NI-DAQmx (Linux + Windows) |
| `instro[labjack]` | LabJack LJM |
| `instro[mccdaq]` | MCC UL (Windows-only) |
| `instro[daq]` | All three DAQ vendor SDKs |
| `instro[aardvark]` | Total Phase Aardvark (I2C); alias: `instro[i2c]` |
| `instro[all]` | Everything above |

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
    psu.output_enable(True, channel=1)
    psu.set_voltage(3.3, channel=1)
    print(psu.get_voltage(channel=1))  # Measurement(channel_data={'my-psu.ch1.voltage': [3.31...]}, ...)
```

For the full walkthrough (including publishing to Nominal Core and the background polling daemon), see the [official documentation](https://instro.nominal.io).

## Supported devices

| Category | Class | Vendors |
|---|---|---|
| Power supply | `InstroPSU` | B&K Precision (9115, 9140), Rigol (DP800-series), Siglent (SPD3303), TDK Lambda (Genesys), simulated |
| Multimeter | `InstroDMM` | Agilent 34401A, Keithley 2400 |
| Electronic load | `InstroELoad` | B&K Precision (85xxB-series) |
| DAQ | `InstroDAQ` | Keysight 34980A, NI-DAQmx, LabJack T-series, MCC USB-series |
| I2C | `I2CInterface` | Total Phase Aardvark |
| Modbus | `ModbusDevice` | Any Modbus TCP / RTU device |

Don't see your vendor? Drivers the maintainers can't verify directly against the device land in [`instro-contrib`](./packages/instro-contrib/) on contributor verification. See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the bar.

## Experimental modules

In-development categories whose APIs may break between releases live in the separate [`instro-unstable`](./packages/instro-unstable/) workspace package:

- **`InstroScope`**: oscilloscope category, with drivers for Keysight 1200x and Tektronix 2-series. Import via `instro.unstable.scope`.
- **`EtherNetIPDevice`**: EtherNet/IP / CIP support for CompactLogix-class PLCs. Import via `instro.unstable.ethernetip`.

Opt in by depending on `instro-unstable` explicitly.

## Documentation

Full guides, API reference, and per-category walkthroughs live at **[instro.nominal.io](https://instro.nominal.io)**.

## Contributing

- **Humans**: see [`CONTRIBUTING.md`](./CONTRIBUTING.md) for development setup, PR conventions, and where different kinds of contributions belong in the workspace.
- **AI coding tools** (Claude Code, Cursor, Codex, Copilot Workspace, …): see [`AGENTS.md`](./AGENTS.md) for codebase landmarks, conventions, and common workflows. The repo ships reusable skills and subagents for both Claude Code (`.claude/`) and Codex CLI (`.agents/`, `.codex/`). The existing skills are `add-instrument-driver` which scaffolds a new vendor driver from a programming manual/API, and `validate-driver-hardware` which smoke-tests an authored driver against the real instrument and self-corrects it. See [Repo skills and subagents](./AGENTS.md#repo-skills-and-subagents).

## License

[Apache License 2.0](./LICENSE). Third-party dependency notices and proprietary vendor runtime requirements are documented in [NOTICE](./NOTICE).
