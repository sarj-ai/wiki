import asyncio
import json
from pathlib import Path
import uuid

import pytest
from typer.testing import CliRunner

from app.document_transform.schemas import TransformMode
from scripts import qa_markdown_transform


def _arguments() -> list[str]:
    return [
        "--markdown-file",
        "source.md",
        "--destination-folder-id",
        "private-folder",
        "--document-name",
        "QA document",
        "--mode",
        "both",
        "--operation-id",
        "da031d91-efca-4d80-8de2-8fd1b34a2ac9",
        "--user-email",
        "reviewer@example.com",
        "--confirm-live-copy",
        "CREATE_LABELED_SIBLING",
    ]


@pytest.mark.parametrize("mode", list(TransformMode))
@pytest.mark.parametrize(
    "email", ["reviewer@example.com", "another@example.com"], ids=["first-user", "second-user"]
)
def test_cli_passes_typed_options(
    mode: TransformMode, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[qa_markdown_transform._Arguments] = []
    output: dict[str, object] = {"status": "completed"}

    async def run(arguments: qa_markdown_transform._Arguments) -> dict[str, object]:
        calls.append(arguments)
        return await asyncio.sleep(0, result=output)

    monkeypatch.setattr(qa_markdown_transform, "_run", run)
    arguments = _arguments()
    arguments[arguments.index("--mode") + 1] = mode.value
    arguments[arguments.index("--user-email") + 1] = email
    result = CliRunner().invoke(qa_markdown_transform.CLI, arguments)
    assert result.exit_code == 0
    assert result.stdout == json.dumps(output, indent=2, sort_keys=True) + "\n"
    assert calls == [
        qa_markdown_transform._Arguments(
            markdown_file=Path("source.md"),
            destination_folder_id="private-folder",
            document_name="QA document",
            mode=mode,
            operation_id=uuid.UUID("da031d91-efca-4d80-8de2-8fd1b34a2ac9"),
            user_email=email,
            confirm_live_copy="CREATE_LABELED_SIBLING",
        )
    ]


@pytest.mark.parametrize(
    "missing",
    [
        "--markdown-file",
        "--destination-folder-id",
        "--document-name",
        "--mode",
        "--operation-id",
        "--confirm-live-copy",
        "--user-email",
    ],
)
def test_required_options_fail_before_execution(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Invalid input must not start a live operation")

    monkeypatch.setattr(qa_markdown_transform, "_run", run)
    arguments = _arguments()
    index = arguments.index(missing)
    del arguments[index : index + 2]
    assert CliRunner().invoke(qa_markdown_transform.CLI, arguments).exit_code == 2


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--mode", "invalid"), ("--mode", "BOTH"), ("--operation-id", "not-a-uuid")],
    ids=["invalid-mode", "uppercase-mode", "invalid-uuid"],
)
def test_invalid_values_never_execute(
    flag: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Invalid input must not start a live operation")

    monkeypatch.setattr(qa_markdown_transform, "_run", run)
    arguments = _arguments()
    arguments[arguments.index(flag) + 1] = value
    assert CliRunner().invoke(qa_markdown_transform.CLI, arguments).exit_code == 2


@pytest.mark.parametrize("confirmation", ["", "create_labeled_sibling", "WRONG"])
def test_confirmation_fails_before_file_or_database_access(
    confirmation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Invalid confirmation must not access files or the database")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(qa_markdown_transform, "async_session", forbidden)
    arguments = _arguments()
    arguments[-1] = confirmation
    result = CliRunner().invoke(qa_markdown_transform.CLI, arguments)
    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert str(result.exception) == "--confirm-live-copy must equal CREATE_LABELED_SIBLING"


def test_missing_project_fails_before_file_or_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Missing project configuration must not access files or the database")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(qa_markdown_transform, "async_session", forbidden)
    monkeypatch.setattr(qa_markdown_transform.settings, "GOOGLE_CLOUD_PROJECT", None)
    result = CliRunner().invoke(qa_markdown_transform.CLI, _arguments())
    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "GOOGLE_CLOUD_PROJECT is required"


@pytest.mark.parametrize("extra", [["--unknown"], ["extra"]], ids=["unknown-option", "positional"])
def test_unexpected_arguments_never_execute(
    extra: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Unexpected arguments must not start a live operation")

    monkeypatch.setattr(qa_markdown_transform, "_run", run)
    assert CliRunner().invoke(qa_markdown_transform.CLI, [*_arguments(), *extra]).exit_code == 2


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_never_executes(flag: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Help must not start a live operation")

    monkeypatch.setattr(qa_markdown_transform, "_run", run)
    result = CliRunner().invoke(qa_markdown_transform.CLI, [flag], env={"TERM": "dumb"})
    assert result.exit_code == 0
    assert "--confirm-live-copy" in result.stdout
