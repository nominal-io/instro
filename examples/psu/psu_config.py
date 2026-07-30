"""Example: three PSUs, three vendors, driven entirely by the JSON files next to this script.

Same loop no matter the vendor -- a fourth supply just means dropping another
psu_config_*.json here, not touching this file.

Edit the visa_resource in psu_config_bk9115.json and psu_config_rigol_dp800.json
for your actual hardware. psu_config_simulated.json runs as-is against the
bundled simulator.

Run:
    uv run python examples/psu/psu_config.py
"""

from pathlib import Path

from instro.psu import InstroPSU

CONFIG_DIR = Path(__file__).parent

for config_path in sorted(CONFIG_DIR.glob("psu_config_*.json")):
    psu = InstroPSU(config=config_path)
    try:
        with psu:
            voltage = psu.get_voltage(channel=1)
            current = psu.get_current(channel=1)
            print(f"{config_path.name}: {psu.name} -> {voltage.latest:.3f}V, {current.latest:.3f}A")
    except Exception as exc:  # noqa: BLE001 - one unreachable PSU shouldn't stop the rest of the bench
        print(f"{config_path.name}: skipped ({exc})")
