import asyncio
import json

import pytest
from typer.testing import CliRunner

from app.document_transform.schemas import TransformMode
from scripts import qa_document_transform


def _arguments() -> list[str]:
    return [
        "--source-id",
        "source-document",
        "--mode",
        "both",
        "--output-name",
        "QA sibling",
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
    calls: list[qa_document_transform._Arguments] = []
    output: dict[str, object] = {"source": {"unchanged": True}}

    async def run(arguments: qa_document_transform._Arguments) -> dict[str, object]:
        calls.append(arguments)
        return await asyncio.sleep(0, result=output)

    monkeypatch.setattr(qa_document_transform, "_run", run)
    arguments = _arguments()
    arguments[3] = mode.value
    arguments[arguments.index("--user-email") + 1] = email
    result = CliRunner().invoke(qa_document_transform.CLI, arguments)
    assert result.exit_code == 0
    assert result.stdout == json.dumps(output, indent=2, sort_keys=True) + "\n"
    assert calls == [
        qa_document_transform._Arguments(
            source_id="source-document",
            mode=mode,
            output_name="QA sibling",
            user_email=email,
            confirm_live_copy="CREATE_LABELED_SIBLING",
        )
    ]


@pytest.mark.parametrize(
    "missing", ["--source-id", "--mode", "--output-name", "--confirm-live-copy", "--user-email"]
)
def test_required_options_fail_before_execution(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Invalid input must not start a live operation")

    monkeypatch.setattr(qa_document_transform, "_run", run)
    arguments = _arguments()
    index = arguments.index(missing)
    del arguments[index : index + 2]
    assert CliRunner().invoke(qa_document_transform.CLI, arguments).exit_code == 2


@pytest.mark.parametrize("mode", ["invalid", "BOTH"])
def test_invalid_mode_fails_before_execution(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Invalid input must not start a live operation")

    monkeypatch.setattr(qa_document_transform, "_run", run)
    arguments = _arguments()
    arguments[3] = mode
    assert CliRunner().invoke(qa_document_transform.CLI, arguments).exit_code == 2


@pytest.mark.parametrize("confirmation", ["", "create_labeled_sibling", "WRONG"])
def test_confirmation_fails_before_database_access(
    confirmation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def session() -> None:
        pytest.fail("Invalid confirmation must not access the database")

    monkeypatch.setattr(qa_document_transform, "async_session", session)
    arguments = _arguments()
    arguments[-1] = confirmation
    result = CliRunner().invoke(qa_document_transform.CLI, arguments)
    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert str(result.exception) == "--confirm-live-copy must equal CREATE_LABELED_SIBLING"


@pytest.mark.parametrize("extra", [["--unknown"], ["extra"]], ids=["unknown-option", "positional"])
def test_unexpected_arguments_never_execute(
    extra: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Unexpected arguments must not start a live operation")

    monkeypatch.setattr(qa_document_transform, "_run", run)
    assert CliRunner().invoke(qa_document_transform.CLI, [*_arguments(), *extra]).exit_code == 2


def test_missing_project_fails_before_database_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def session() -> None:
        pytest.fail("Missing project configuration must not access the database")

    monkeypatch.setattr(qa_document_transform, "async_session", session)
    monkeypatch.setattr(qa_document_transform.settings, "GOOGLE_CLOUD_PROJECT", None)
    result = CliRunner().invoke(qa_document_transform.CLI, _arguments())
    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "GOOGLE_CLOUD_PROJECT is required"


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_never_executes(flag: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_args: object) -> None:
        pytest.fail("Help must not start a live operation")

    monkeypatch.setattr(qa_document_transform, "_run", run)
    result = CliRunner().invoke(qa_document_transform.CLI, [flag], env={"TERM": "dumb"})
    assert result.exit_code == 0
    assert "--confirm-live-copy" in result.stdout
