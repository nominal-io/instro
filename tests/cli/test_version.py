import importlib.metadata
from unittest.mock import patch

from typer.testing import CliRunner

from instro.cli.main import _WORKSPACE_PACKAGES, app

runner = CliRunner()


def test_version_lists_core_and_every_workspace_package():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("instro ")
    for package, _ in _WORKSPACE_PACKAGES:
        assert package in result.output


def test_version_marks_missing_packages_with_extra_hint():
    def fake_version(name: str) -> str:
        if name == "instro-daq-ni":
            raise importlib.metadata.PackageNotFoundError(name)
        return "1.2.3"

    with patch("importlib.metadata.version", side_effect=fake_version):
        result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert 'instro-daq-ni not installed (pip install "instro[nidaq]")' in result.output
    assert "instro-contrib 1.2.3" in result.output


def test_version_is_eager_and_skips_commands():
    with patch("instro.cli.main.discover") as mock_discover:
        result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    mock_discover.assert_not_called()
