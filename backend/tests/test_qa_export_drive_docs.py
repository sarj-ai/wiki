import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripts import qa_export_drive_docs


@pytest.mark.parametrize(
    "user_email", [None, "reviewer@sarj.ai"], ids=["default-user", "explicit-user"]
)
def test_export_arguments_preserve_order_and_output(
    user_email: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exports = [{"file_id": "second", "path": "report.pdf"}]
    calls: list[tuple[list[str], Path, str]] = []

    async def run(file_ids: list[str], output_dir: Path, email: str) -> list[dict[str, str]]:
        calls.append((file_ids, output_dir, email))
        return await asyncio.sleep(0, result=exports)

    monkeypatch.setattr(qa_export_drive_docs, "_run", run)
    output = tmp_path / "exports"
    arguments = ["--file-id", "second", "--file-id", "first", "--output-dir", str(output)]
    if user_email is not None:
        arguments.extend(["--user-email", user_email])
    result = CliRunner().invoke(qa_export_drive_docs.CLI, arguments)
    assert result.exit_code == 0
    assert result.stdout == json.dumps(exports, indent=2, sort_keys=True) + "\n"
    assert calls == [(["second", "first"], output, user_email or "nasr@sarj.ai")]
    assert not output.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--file-id", "document"],
        ["--output-dir", "exports"],
        ["--file-id", "document", "--output-dir", "exports", "--unknown"],
        ["--file-id", "document", "--output-dir", "exports", "extra"],
        ["--file-id", "--output-dir", "exports"],
    ],
    ids=[
        "missing-both",
        "missing-output",
        "missing-file",
        "unknown-option",
        "extra-argument",
        "missing-value",
    ],
)
def test_invalid_arguments_do_not_export(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Invalid arguments must not start an export")

    monkeypatch.setattr(qa_export_drive_docs, "_run", run)
    result = CliRunner().invoke(qa_export_drive_docs.CLI, arguments)
    assert result.exit_code == 2


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_does_not_export(flag: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Help must not start an export")

    monkeypatch.setattr(qa_export_drive_docs, "_run", run)
    result = CliRunner().invoke(qa_export_drive_docs.CLI, [flag], env={"TERM": "dumb"})
    assert result.exit_code == 0
    assert "--file-id" in result.stdout
