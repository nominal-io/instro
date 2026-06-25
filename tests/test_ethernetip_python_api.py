"""Tests for the local Python API surface that do not require a PLC simulator."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from instro.unstable._ethernetip import StructuredValue

TYPECHECK_DIR = Path(__file__).with_name("typecheck")


def test_structured_value_bytes_conversion() -> None:
    """StructuredValue supports the runtime bytes protocol advertised in the stub."""
    assert bytes(StructuredValue(symbol_id=7, data=b"\x01\x02\x03")) == b"\x01\x02\x03"


def test_ethernetip_stub_matches_runtime_boundaries(tmp_path: Path) -> None:
    """The private local stub accepts supported APIs and rejects string misuse."""
    pytest.importorskip("mypy", reason="mypy is required to validate exported type information")

    repo_root = Path(__file__).resolve().parents[1]
    config = tmp_path / "mypy.ini"
    config.write_text(
        "\n".join(
            [
                "[mypy]",
                "namespace_packages = True",
                "explicit_package_bases = True",
                "mypy_path = "
                + os.pathsep.join(
                    [
                        str(repo_root),
                        str(repo_root / "packages/instro-ethernetip"),
                    ]
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )

    valid_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            f"--config-file={config}",
            str(TYPECHECK_DIR / "ethernetip_valid.py"),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert valid_result.returncode == 0, valid_result.stdout + valid_result.stderr

    invalid_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            f"--config-file={config}",
            str(TYPECHECK_DIR / "ethernetip_invalid_read_tags.py"),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    invalid_output = invalid_result.stdout + invalid_result.stderr
    assert invalid_result.returncode != 0, invalid_output
    assert "read_tags" in invalid_output
    assert "write_tag" in invalid_output
    assert "str" in invalid_output
