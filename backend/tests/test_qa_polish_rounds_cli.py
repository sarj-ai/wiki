from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripts import qa_polish_rounds


@pytest.fixture
def ledger_file(tmp_path: Path) -> Path:
    ledger = qa_polish_rounds.ImprovementLedger(
        seed="seed",
        private_folder_id="private-folder",
        expected_source_ids=("source-a", "source-b", "source-c"),
        rounds=(),
    )
    path = tmp_path / "ledger.json"
    path.write_text(ledger.model_dump_json(), encoding="utf-8")
    return path


def test_incomplete_evidence_fails_closed(ledger_file: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        qa_polish_rounds.CLI, [str(ledger_file), "--artifacts-root", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert result.stderr == "INCOMPLETE: 0/5 evidenced improvement rounds\n"
    assert not result.stdout


def test_success_requires_verifier_result(
    ledger_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[qa_polish_rounds.ImprovementLedger, Path]] = []

    def verify(ledger: qa_polish_rounds.ImprovementLedger, artifacts_root: Path) -> int:
        calls.append((ledger, artifacts_root))
        return 5

    monkeypatch.setattr(qa_polish_rounds, "verify_ledger", verify)
    result = CliRunner().invoke(
        qa_polish_rounds.CLI, [str(ledger_file), "--artifacts-root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert result.stdout == "PASS: 5/5 evidenced improvement rounds\n"
    assert calls == [
        (
            qa_polish_rounds.ImprovementLedger.model_validate_json(
                ledger_file.read_text(encoding="utf-8")
            ),
            tmp_path,
        )
    ]


@pytest.mark.parametrize(
    "arguments",
    [[], ["ledger.json"], ["--artifacts-root", "artifacts"], ["ledger.json", "--unknown"]],
    ids=["missing-both", "missing-artifacts", "missing-ledger", "unknown-option"],
)
def test_invalid_arguments_are_usage_errors(arguments: list[str]) -> None:
    result = CliRunner().invoke(qa_polish_rounds.CLI, arguments)
    assert result.exit_code == 2
    assert not result.stdout


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_does_not_read_evidence(flag: str) -> None:
    result = CliRunner().invoke(qa_polish_rounds.CLI, [flag], env={"TERM": "dumb"})
    assert result.exit_code == 0
    assert "--artifacts-root" in result.stdout


@pytest.mark.parametrize("content", ["not json", "{}"], ids=["invalid-json", "invalid-ledger"])
def test_malformed_evidence_never_passes(content: str, tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(content, encoding="utf-8")
    result = CliRunner().invoke(
        qa_polish_rounds.CLI, [str(path), "--artifacts-root", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert not result.stdout
