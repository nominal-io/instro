"""Example: build an InstroAWG from a JSON config file.

Requires real hardware: edit bench_awg.json's visa_resource for your instrument first.

Run:
    uv run python packages/instro-unstable/instro/unstable/awg/examples/awg_config.py
"""

from pathlib import Path

from instro.unstable.awg import InstroAWG

CONFIG_PATH = Path(__file__).parent / "bench_awg.json"


def main() -> None:
    with InstroAWG(config=CONFIG_PATH) as awg:
        awg.output_enable(1, True)
        waveform = awg.get_waveform(1)
        print(f"{awg.name} ch1: {waveform}")
        awg.output_enable(1, False)


if __name__ == "__main__":
    main()
