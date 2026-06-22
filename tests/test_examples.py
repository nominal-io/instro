"""Tests for checked-in example assets."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from instro.modbus import ModbusConfig
from instro.unstable.ethernetip import EtherNetIPConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
EXAMPLE_JSON_CONFIGS = sorted(EXAMPLES_DIR.rglob("*.json"))
ETHERNETIP_EXAMPLES = sorted((EXAMPLES_DIR / "ethernetip").glob("*.py"))
ETHERNETIP_MYPY_PATHS = [
    REPO_ROOT,
    REPO_ROOT / "packages/instro-unstable",
    REPO_ROOT / "packages/instro-ethernetip",
]

CONFIG_LOADERS = {
    "modbus": ModbusConfig,
    "ethernetip": EtherNetIPConfig,
}


@pytest.mark.parametrize("config_path", EXAMPLE_JSON_CONFIGS, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_example_json_configs_parse(config_path: Path) -> None:
    """Example JSON configs should be valid for the protocol they declare."""
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    protocol = raw_config.get("protocol")

    assert protocol in CONFIG_LOADERS, f"{config_path} declares unsupported protocol {protocol!r}"

    config = CONFIG_LOADERS[protocol].from_json(config_path)
    assert config.protocol == protocol


def test_ethernetip_examples_type_check(tmp_path: Path) -> None:
    """EtherNet/IP examples should keep matching the public unstable Python API."""
    pytest.importorskip("mypy", reason="mypy is required to type-check examples")

    config = tmp_path / "mypy.ini"
    config.write_text(
        "\n".join(
            [
                "[mypy]",
                "namespace_packages = True",
                "explicit_package_bases = True",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            f"--config-file={config}",
            *map(str, ETHERNETIP_EXAMPLES),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "MYPYPATH": os.pathsep.join(map(str, ETHERNETIP_MYPY_PATHS)),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
