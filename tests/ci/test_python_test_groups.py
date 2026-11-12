import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("python_test_groups.py")


def test_discovers_whole_directories_and_root_files(tmp_path: Path) -> None:
    for name in (
        "tests/awg/test_awg.py",
        "tests/awg/test_other.py",
        "tests/daq/vendor/test_daq.py",
        "tests/opcua/test_client.py",
        "tests/opcua/test_types.py",
        "tests/path with spaces/test_driver.py",
        "tests/test_examples.py",
        "tests/test_exceptions.py",
        "tests/__pycache__/cache.pyc",
        "tests/fixtures/data.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True, check=True)

    assert json.loads(result.stdout) == [
        {"name": "group-1", "paths": ["tests/awg", "tests/test_examples.py"]},
        {"name": "group-2", "paths": ["tests/daq", "tests/test_exceptions.py"]},
        {"name": "group-3", "paths": ["tests/opcua"]},
        {"name": "group-4", "paths": ["tests/path with spaces"]},
    ]


def test_rejects_empty_suite(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()

    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True)

    assert result.returncode != 0
    assert "No Python test paths found" in result.stderr
