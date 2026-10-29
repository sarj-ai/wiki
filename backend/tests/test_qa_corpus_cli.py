import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.document_transform.schemas import TransformMode
from scripts import qa_document_transform_corpus as corpus
from tests.test_qa_document_transform_corpus import _evidence_manifest


def _arguments() -> list[str]:
    return [
        "--seed",
        "qa",
        "--expected-release-sha",
        "a" * 40,
        "--manifest",
        "manifest.json",
        "--ledger",
        "ledger.jsonl",
        "--source-folder-id",
        "source-folder",
        "--destination-folder-id",
        "private-folder",
        "--user-email",
        "reviewer@example.com",
    ]


def _expected_arguments() -> corpus._Arguments:
    return corpus._Arguments(
        seed="qa",
        expected_release_sha="a" * 40,
        manifest=Path("manifest.json"),
        ledger=Path("ledger.jsonl"),
        source_folder_id="source-folder",
        destination_folder_id="private-folder",
        user_email="reviewer@example.com",
    )


def test_parser_preserves_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[corpus._Arguments] = []
    monkeypatch.setattr(corpus, "_report", calls.append)
    result = CliRunner().invoke(corpus.CLI, _arguments())
    assert result.exit_code == 0
    assert calls == [_expected_arguments()]


@pytest.mark.parametrize("mode", list(TransformMode))
@pytest.mark.parametrize("execute", [False, True], ids=["plan", "execute"])
@pytest.mark.parametrize("canary", [False, True], ids=["corpus", "canary"])
def test_parser_preserves_all_explicit_options(
    mode: TransformMode, execute: bool, canary: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[corpus._Arguments] = []
    monkeypatch.setattr(corpus, "_report", calls.append)
    arguments = [
        *_arguments(),
        "--count",
        "1",
        "--mode",
        mode.value,
        "--canary-manifest",
        "canary.json",
        "--canary-ledger",
        "canary.jsonl",
        "--canary-artifacts-dir",
        "canary-artifacts",
        "--artifacts-dir",
        "artifacts",
        "--pdftoppm-version",
        "25.06.0",
        "--judge-model",
        "gemini-3.8-flash",
        "--confirm-live-copy",
        "confirmation",
    ]
    if execute:
        arguments.append("--execute")
    if canary:
        arguments.append("--canary")
    assert CliRunner().invoke(corpus.CLI, arguments).exit_code == 0
    assert calls == [
        replace(
            _expected_arguments(),
            count=1,
            mode=mode,
            execute=execute,
            canary=canary,
            canary_manifest=Path("canary.json"),
            canary_ledger=Path("canary.jsonl"),
            canary_artifacts_dir=Path("canary-artifacts"),
            artifacts_dir=Path("artifacts"),
            pdftoppm_version="25.06.0",
            judge_model="gemini-3.8-flash",
            confirm_live_copy="confirmation",
        )
    ]


@pytest.mark.parametrize(
    "missing",
    [
        "--seed",
        "--expected-release-sha",
        "--manifest",
        "--ledger",
        "--source-folder-id",
        "--destination-folder-id",
        "--user-email",
    ],
)
def test_required_options_never_execute(missing: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def report(_arguments: corpus._Arguments) -> None:
        pytest.fail("Invalid input must not execute")

    monkeypatch.setattr(corpus, "_report", report)
    arguments = _arguments()
    index = arguments.index(missing)
    del arguments[index : index + 2]
    assert CliRunner().invoke(corpus.CLI, arguments).exit_code == 2


@pytest.mark.parametrize(
    "extra",
    [
        ["--mode", "invalid"],
        ["--mode", "BOTH"],
        ["--count", "invalid"],
        ["--unknown"],
        ["extra"],
        ["--execute", "false"],
    ],
    ids=["invalid-mode", "uppercase-mode", "invalid-count", "unknown", "positional", "flag-value"],
)
def test_invalid_options_never_execute(extra: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    def report(_arguments: corpus._Arguments) -> None:
        pytest.fail("Invalid input must not execute")

    monkeypatch.setattr(corpus, "_report", report)
    assert CliRunner().invoke(corpus.CLI, [*_arguments(), *extra]).exit_code == 2


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        (["--execute"], "--confirm-live-copy_must_equal_CREATE_ALL_OWNER_ONLY_DESTINATION_COPIES"),
        (
            ["--execute", "--canary", "--count", "1"],
            "--confirm-live-copy_must_equal_CREATE_1_PRIVATE_CANARY",
        ),
        (["--canary"], "--count_must_equal_1_for_canary_QA"),
        (["--count", "1"], "--count_must_equal_20_for_corpus_QA"),
        (["--confirm-live-copy", "WRONG"], "--confirm-live-copy_is_only_valid_with_--execute"),
        (
            [],
            "the_20-document_corpus_requires_--canary-manifest_--canary-ledger_and_--canary-artifacts-dir",
        ),
    ],
    ids=[
        "corpus-confirmation",
        "canary-confirmation",
        "canary-count",
        "corpus-count",
        "plan-confirmation",
        "canary-evidence",
    ],
)
def test_real_guards_reject_before_database_access(
    extra: list[str], error: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def session() -> None:
        pytest.fail("Rejected QA arguments must not access the database")

    monkeypatch.setattr(corpus, "async_session", session)
    result = CliRunner().invoke(corpus.CLI, [*_arguments(), *extra])
    assert result.exit_code == 1
    assert not result.stdout
    assert json.loads(result.stderr) == {"error": error, "status": "failed"}


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_never_executes(flag: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def report(_arguments: corpus._Arguments) -> None:
        pytest.fail("Help must not execute")

    monkeypatch.setattr(corpus, "_report", report)
    result = CliRunner().invoke(corpus.CLI, [flag], env={"TERM": "dumb"})
    assert result.exit_code == 0
    assert "--confirm-live-copy" in result.stdout
    assert "--count" not in result.stdout


def test_success_preserves_summary_json(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _evidence_manifest()

    async def run(_arguments: corpus._Arguments) -> corpus.EvidenceManifest:
        return await asyncio.sleep(0, result=manifest)

    monkeypatch.setattr(corpus, "_run", run)
    result = CliRunner().invoke(corpus.CLI, _arguments())
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "cohort": "canary",
        "expected_release_sha": "a" * 40,
        "planned": 1,
        "verified": 0,
        "manifest_plan_sha256": corpus._manifest_plan_sha256(manifest),
    }


def test_unexpected_error_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(_arguments: corpus._Arguments) -> None:
        message = "private document detail"
        raise RuntimeError(message)

    monkeypatch.setattr(corpus, "_run", run)
    result = CliRunner().invoke(corpus.CLI, _arguments())
    assert result.exit_code == 1
    assert not result.stdout
    assert json.loads(result.stderr) == {"error": "internal_error", "status": "failed"}
