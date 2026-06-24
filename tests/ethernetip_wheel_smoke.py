# Used by the `just eip-wheel-smoke-test` recipe.

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

from instro.unstable._ethernetip import EtherNetIpSession, PlcKind, PlcValue, StructuredValue

TYPECHECK_DIR = Path(__file__).with_name("typecheck")


def _assert_wheel_contains_expected_files(wheel_path: Path) -> None:
    with ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
    name_set = set(names)

    required = {
        "instro/py.typed",
        "instro/unstable/_ethernetip.pyi",
    }
    missing = sorted(required - name_set)
    if missing:
        raise AssertionError(f"{wheel_path.name} is missing: {', '.join(missing)}")

    native_files = [
        name for name in names if name.startswith("instro/unstable/_ethernetip.") and name.endswith((".so", ".pyd"))
    ]
    if len(native_files) != 1:
        raise AssertionError(f"{wheel_path.name} expected one native extension, found {native_files}")

    for filename in ("METADATA", "WHEEL", "RECORD"):
        if not any(name.startswith("instro_ethernetip-") and name.endswith(f".dist-info/{filename}") for name in names):
            raise AssertionError(f"{wheel_path.name} is missing dist-info/{filename}")


def _run_mypy(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy", "--namespace-packages", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_installed_wheel_types() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        valid_fixture = workspace / "ethernetip_valid.py"
        invalid_fixture = workspace / "ethernetip_invalid_read_tags.py"
        shutil.copyfile(TYPECHECK_DIR / "ethernetip_valid.py", valid_fixture)
        shutil.copyfile(TYPECHECK_DIR / "ethernetip_invalid_read_tags.py", invalid_fixture)

        valid_result = _run_mypy([str(valid_fixture)], workspace)
        assert valid_result.returncode == 0, valid_result.stdout + valid_result.stderr

        invalid_result = _run_mypy([str(invalid_fixture)], workspace)
        invalid_output = invalid_result.stdout + invalid_result.stderr
        assert invalid_result.returncode != 0, invalid_output
        assert "read_tags" in invalid_output
        assert "write_tag" in invalid_output
        assert "str" in invalid_output


def main() -> None:
    wheel = os.environ.get("INSTRO_EIP_WHEEL")
    if not wheel:
        raise AssertionError("INSTRO_EIP_WHEEL must point to the built instro-ethernetip wheel")

    _assert_wheel_contains_expected_files(Path(wheel))

    assert EtherNetIpSession.__name__ == "EtherNetIpSession"
    assert PlcKind.__name__ == "PlcKind"
    assert PlcValue.__name__ == "PlcValue"
    assert StructuredValue.__name__ == "StructuredValue"
    _assert_installed_wheel_types()
    print("PASS: local EtherNet/IP wheel contents, imports, and type stubs verified")


if __name__ == "__main__":
    main()
