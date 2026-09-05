from copy import deepcopy
import json
from pathlib import Path
import runpy
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).parents[2]
MODULE = runpy.run_path(str(ROOT / "scripts/github/read-transform-diagnostics.py"))
TASK = "759ac71e-125b-47bf-a5d4-be45b9d189f2"
REQUEST = "21f0d509cb73"


def _record() -> dict[str, Any]:
    return {
        "timestamp": "2026-09-05T00:12:10.927Z",
        "jsonPayload": {
            "record": {
                "message": "document copy verification failed checks=PRIVATE DATA",
                "extra": {
                    "task_id": TASK,
                    "request_id": REQUEST,
                    "checks": {"content_preserved": False},
                    "diagnostics": {"bullet_font_mismatch": 1},
                    "payload": "SECRET",
                    "title": "SECRET",
                    "token": "SECRET",
                },
            }
        },
    }


def test_only_static_typed_fields_are_emitted() -> None:
    rows = MODULE["sanitize"]([_record()], TASK)
    assert rows[0]["content_preserved"] is False
    assert rows[0]["bullet_font_mismatch"] == 1
    assert "SECRET" not in json.dumps(rows)
    assert "PRIVATE" not in json.dumps(rows)
    assert "message" not in rows[0]


def test_emphasis_breakdown_is_fixed_counter_allowlist() -> None:
    record = _record()
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {
        "bullet_bold_added_false": 3,
        "bullet_italic_removed_true": 1,
        "bullet_underline_request_expected_false": 2,
        "bullet_private_style_changed": 1,
    }
    row = MODULE["sanitize"]([record], TASK)[0]
    assert row["bullet_bold_added_false"] == 3
    assert row["bullet_italic_removed_true"] == 1
    assert row["bullet_underline_request_expected_false"] == 2
    assert "bullet_private_style_changed" not in row


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bullet_font_mismatch", True),
        ("bullet_font_mismatch", -1),
        ("bullet_font_mismatch", 1000001),
        ("content_preserved", "false"),
        ("content_preserved", 0),
    ],
)
def test_malformed_diagnostics_fail_closed(field: str, value: object) -> None:
    record = _record()
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {field: value}
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


@pytest.mark.parametrize(
    "value",
    ["x", TASK + '" OR severity>=0', TASK.upper(), "$(id)"],
    ids=["short", "query", "uppercase", "shell"],
)
def test_invalid_uuid_never_reaches_gcloud(value: str) -> None:
    with pytest.raises(ValueError, match=r"^($|badly formed hexadecimal UUID string$)"):
        MODULE["validate_task_id"](value)


def test_exact_derived_request_correlation_only() -> None:
    record = _record()
    extra = record["jsonPayload"]["record"]["extra"]
    extra.pop("task_id")
    assert MODULE["sanitize"]([record], TASK, frozenset({REQUEST}))[0]["task_id"] == TASK
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)
    extra["task_id"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK, frozenset({REQUEST}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", 'abc" OR severity>=0'),
        ("error_code", "user-controlled-error"),
    ],
)
def test_unsafe_metadata_is_rejected(field: str, value: str) -> None:
    record = _record()
    record["jsonPayload"]["record"]["extra"][field] = value
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


def test_unrecognized_messages_are_not_diagnostic_sources() -> None:
    record = deepcopy(_record())
    record["jsonPayload"]["record"]["message"] = "user submitted diagnostic payload"
    assert MODULE["sanitize"]([record], TASK) == []


def test_lifecycle_drives_bounded_correlated_lookup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lifecycle = _record()
    lifecycle["jsonPayload"]["record"]["message"] = (
        f"document transform started task_id={TASK} attempt=1"
    )
    diagnostic = _record()
    diagnostic["jsonPayload"]["record"]["extra"].pop("task_id")
    calls = []

    def read(clause: str, limit: int) -> object:
        calls.append((clause, limit))
        return [lifecycle] if len(calls) == 1 else [diagnostic]

    main = MODULE["main"]
    monkeypatch.setitem(main.__globals__, "read_records", read)
    monkeypatch.setenv("QA_TASK_ID", TASK)
    monkeypatch.setattr(main.__globals__["sys"], "argv", ["read-transform-diagnostics.py"])
    assert main() == 0
    assert calls == [
        (f'jsonPayload.record.extra.task_id="{TASK}"', 200),
        (f'jsonPayload.record.extra.request_id="{REQUEST}"', 199),
    ]
    output = capsys.readouterr().out
    assert json.loads(output)["returned_count"] == 2
    assert "PRIVATE" not in output
    assert "SECRET" not in output


def test_workflow_uses_main_only_existing_wif_and_no_input_shell_interpolation() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/read-transform-diagnostics.yml").read_text()
    )
    job = workflow["jobs"]["diagnostics"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["env"] == {"QA_TASK_ID": "${{ inputs.task_id }}"}
    steps = job["steps"]
    assert steps[0]["with"]["persist-credentials"] is False
    assert steps[1]["run"].endswith("--validate-only")
    assert steps[2]["with"] == {
        "workload_identity_provider": "${{ secrets.WIF_PROVIDER }}",
        "service_account": "${{ secrets.WIF_SERVICE_ACCOUNT }}",
    }
    assert all("${{" not in step.get("run", "") for step in steps)
