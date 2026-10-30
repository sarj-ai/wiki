from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripts import export_openapi


def test_render_is_deterministic() -> None:
    assert export_openapi._render_spec() == export_openapi._render_spec()


def test_check_does_not_modify_stale_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "openapi.json"
    output.write_text("stale", encoding="utf-8")
    result = CliRunner().invoke(export_openapi.CLI, ["--check", str(output)])
    assert result.exit_code == 1
    assert result.stderr == f"OpenAPI schema is out of date: {output}\n"
    assert output.read_text(encoding="utf-8") == "stale"


def test_export_then_check(tmp_path: Path) -> None:
    output = tmp_path / "openapi.json"
    runner = CliRunner()
    assert runner.invoke(export_openapi.CLI, [str(output)]).exit_code == 0

    assert runner.invoke(export_openapi.CLI, ["--check", str(output)]).exit_code == 0


def test_check_does_not_create_missing_output(tmp_path: Path) -> None:
    output = tmp_path / "missing.json"
    result = CliRunner().invoke(export_openapi.CLI, [str(output), "--check"])
    assert result.exit_code == 1
    assert not output.exists()


def test_default_output_stays_relative_to_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    monkeypatch.chdir(backend)
    runner = CliRunner()
    assert runner.invoke(export_openapi.CLI, []).exit_code == 0
    assert (tmp_path / "frontend" / "openapi.json").is_file()
    assert runner.invoke(export_openapi.CLI, ["--check"]).exit_code == 0


@pytest.mark.parametrize(
    "arguments",
    [["--unknown"], ["one.json", "two.json"], ["--no-check"], ["--check=true"]],
    ids=["unknown-option", "extra-path", "unsupported-negative-flag", "flag-with-value"],
)
def test_invalid_arguments_do_not_export(
    arguments: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(export_openapi.CLI, arguments)
    assert result.exit_code == 2
    assert not Path("one.json").exists()


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_does_not_export(flag: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    monkeypatch.chdir(backend)
    result = CliRunner().invoke(export_openapi.CLI, [flag], env={"TERM": "dumb"})
    assert result.exit_code == 0
    assert "--check" in result.stdout
    assert not (tmp_path / "frontend").exists()
