import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("fixture", ["opcua_valid.py", "opcua_invalid.py"])
def test_public_type_contracts(tmp_path: Path, fixture: str) -> None:
    config = tmp_path / "mypy.ini"
    config.write_text("[mypy]\nnamespace_packages = True\nexplicit_package_bases = True\n", encoding="utf-8")
    shutil.copyfile(Path(__file__).with_name("typecheck") / fixture, tmp_path / fixture)
    env = os.environ.copy()
    env.pop("MYPYPATH", None)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(config), fixture],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr

    if fixture == "opcua_valid.py":
        assert result.returncode == 0, output
    else:
        assert result.returncode != 0, output

        for rejected in ("username", "session_timeout", "get_node", "poll_frequency", "assignment"):
            assert rejected in output, output

        for constructor in ("IdentityToken", "Node", "AttributeValue", "RolePermissionType"):
            assert f'Missing positional argument "_unavailable" in call to "{constructor}"' in output, output


def test_public_stubs_match_native_signatures(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mypy.stubtest", "instro.unstable.opcua"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
