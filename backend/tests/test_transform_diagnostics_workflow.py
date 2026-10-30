from copy import deepcopy
import json
from pathlib import Path
import runpy
import subprocess
import sys
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


@pytest.mark.parametrize("invalid", [False, True])
def test_header_recovery_diagnostics_are_finite_and_private(*, invalid: bool) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document header recovery checks=SECRET"
    record["jsonPayload"]["record"]["extra"]["checks"] = {
        "header_recovery_reason": "SECRET" if invalid else "inventory",
        "header_recovery_empty_reason": "paragraph_style",
        "header_recovery_body_equal": True,
        "header_recovery_paragraph_spaceAbove_unit_only": True,
        "header_recovery_paragraph_spaceAbove_explicit_zero": False,
        "header_recovery_run_style_empty": False,
        "raw_header": "SECRET",
    }
    if invalid:
        with pytest.raises(ValueError, match=r"^$"):
            MODULE["sanitize"]([record], TASK)
    else:
        rows = MODULE["sanitize"]([record], TASK)
        assert rows[0]["header_recovery_reason"] == "inventory"
        assert rows[0]["header_recovery_empty_reason"] == "paragraph_style"
        assert rows[0]["header_recovery_body_equal"] is True
        assert rows[0]["header_recovery_paragraph_spaceAbove_unit_only"] is True
        assert rows[0]["header_recovery_paragraph_spaceAbove_explicit_zero"] is False
        assert rows[0]["header_recovery_run_style_empty"] is False
        assert "SECRET" not in json.dumps(rows)


def test_native_font_proof_fields_are_finite_and_private() -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document native font proof checks=SECRET"
    record["jsonPayload"]["record"]["extra"]["checks"] = {
        "font_proof_count": 57,
        "font_proof_verified_count": 4,
        "font_proof_reason": "paragraph_payload",
        "font_proof_stage": "content",
        "font_proof_valid": False,
        "supported_brand_applied": True,
        "raw_styles": "SECRET",
    }
    rows = MODULE["sanitize"]([record], TASK)
    assert rows[0]["font_proof_verified_count"] == 4
    assert rows[0]["font_proof_reason"] == "paragraph_payload"
    assert rows[0]["supported_brand_applied"] is True
    assert "SECRET" not in json.dumps(rows)


@pytest.mark.parametrize(
    "field",
    [
        "font_proof_components_evaluated",
        "font_proof_paragraph_metadata_equal",
        "font_proof_visible_residual_equal",
        "font_proof_terminal_residual_equal",
        "font_proof_terminal_family_representation_equal",
        "font_proof_parent_context_equal",
    ],
)
def test_component_diagnostics_allow_only_booleans_without_fingerprints(field: str) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document native font proof checks=SECRET"
    checks: dict[str, object] = {
        field: False,
        "visible_residual_sha256": "a" * 64,
        "components": {"SECRET": "SECRET"},
    }
    record["jsonPayload"]["record"]["extra"]["checks"] = checks
    rows = MODULE["sanitize"]([record], TASK)
    assert rows[0][field] is False
    assert "a" * 64 not in json.dumps(rows)
    assert "SECRET" not in json.dumps(rows)
    checks[field] = 0
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("font_proof_reason", "SECRET"),
        ("font_proof_stage", "SECRET"),
        ("font_proof_count", True),
        ("font_proof_count", 751),
        ("font_proof_verified_count", -1),
        ("font_proof_valid", 1),
        ("supported_brand_applied", "SECRET"),
    ],
)
def test_native_font_diagnostics_reject_unbounded_or_untyped_values(
    field: str, value: object
) -> None:
    record = _record()
    record["jsonPayload"]["record"]["extra"]["checks"] = {field: value}
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


@pytest.mark.parametrize(
    "value", [0, 1, "SECRET", None, {}], ids=["zero", "one", "string", "null", "object"]
)
def test_spacer_style_value_diagnostics_reject_nonboolean_values(value: object) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document spacer eligibility"
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {
        "spacer_named_border_all_padding_zero": value,
    }
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


@pytest.mark.parametrize("invalid", [False, True])
def test_native_import_diagnostics_expose_only_static_boolean_checks(*, invalid: bool) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = (
        "document markdown import verification checks=SECRET"
    )
    record["jsonPayload"]["record"]["extra"]["checks"] = {
        "import_header_shape": False,
        "import_header_title_style": True,
        "import_header_space_above_unit_only": True,
        "import_header_keep_next_absent": False,
        "import_header_run_fields_known": False,
        "import_logo_dimensions": True,
        "import_logo_title_exact": True,
        "import_logo_description_exact": False,
        "import_logo_image_properties_valid": True,
        "import_logo_source_uri_absent": True,
        "import_logo_source_uri_exact": False,
        "import_logo_metadata": "SECRET" if invalid else False,
        "import_body_style": 1 if invalid else True,
        "provider_body": "SECRET",
    }
    if invalid:
        with pytest.raises(ValueError, match=r"^$"):
            MODULE["sanitize"]([record], TASK)
        return
    rows = MODULE["sanitize"]([record], TASK)
    assert rows[0]["import_header_shape"] is False
    assert rows[0]["import_header_title_style"] is True
    assert rows[0]["import_header_space_above_unit_only"] is True
    assert rows[0]["import_header_keep_next_absent"] is False
    assert rows[0]["import_header_run_fields_known"] is False
    assert rows[0]["import_logo_dimensions"] is True
    assert rows[0]["import_logo_metadata"] is False
    assert rows[0]["import_logo_title_exact"] is True
    assert rows[0]["import_logo_description_exact"] is False
    assert rows[0]["import_logo_image_properties_valid"] is True
    assert rows[0]["import_logo_source_uri_absent"] is True
    assert rows[0]["import_logo_source_uri_exact"] is False
    assert rows[0]["import_body_style"] is True
    assert "SECRET" not in json.dumps(rows)


@pytest.mark.parametrize("invalid", [False, True])
def test_native_safety_diagnostics_keep_only_finite_stage_reason(invalid: bool) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document native safety stop stage=SECRET"
    record["jsonPayload"]["record"]["extra"].update(
        {
            "safety_stage": "SECRET" if invalid else "footer_projection_empty",
            "safety_reason": "invariant",
            "raw_provider_message": "SECRET",
        }
    )
    if invalid:
        with pytest.raises(ValueError, match=r"^$"):
            MODULE["sanitize"]([record], TASK)
    else:
        row = MODULE["sanitize"]([record], TASK)[0]
        assert row["safety_stage"] == "footer_projection_empty"
        assert row["safety_reason"] == "invariant"
        assert "SECRET" not in json.dumps(row)


@pytest.mark.parametrize("invalid", [False, True])
@pytest.mark.parametrize("invalid_field", ["footer_shape_check", "footer_shape_reason"])
def test_footer_shape_diagnostics_are_finite_and_private(invalid: bool, invalid_field: str) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document native safety stop stage=SECRET"
    record["jsonPayload"]["record"]["extra"].update(
        {
            "safety_stage": "footer_projection_populated",
            "safety_reason": "shape",
            "footer_shape_check": "font_family",
            "footer_shape_reason": "missing",
            "actual_font": "SECRET",
            "caption": "SECRET",
        }
    )
    if invalid:
        record["jsonPayload"]["record"]["extra"][invalid_field] = "SECRET"
        with pytest.raises(ValueError, match=r"^$"):
            MODULE["sanitize"]([record], TASK)
    else:
        row = MODULE["sanitize"]([record], TASK)[0]
        assert row["footer_shape_check"] == "font_family"
        assert row["footer_shape_reason"] == "missing"
        assert "SECRET" not in json.dumps(row)


def test_in_place_preflight_exposes_only_static_reasons_and_counts() -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document in-place preflight checks=SECRET"
    record["jsonPayload"]["record"]["extra"]["checks"] = {
        "in_place_unknown_header": True,
        "in_place_missing_logo": False,
        "unknown_header_count": 3,
        "missing_header_count": 0,
        "private_header_text": "SECRET",
    }
    row = MODULE["sanitize"]([record], TASK)[0]
    assert row["in_place_unknown_header"] is True
    assert row["in_place_missing_logo"] is False
    assert row["unknown_header_count"] == 3
    assert row["missing_header_count"] == 0
    assert "SECRET" not in json.dumps(row)


def test_provider_boundary_diagnostics_are_finite_and_private() -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = (
        "document stage provider boundary http_status=400 SECRET"
    )
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {
        "provider_category": "date_properties",
        "provider_operation": "insertDate",
        "provider_request_ordinal": 2,
        "provider_message": "SECRET",
    }
    row = MODULE["sanitize"]([record], TASK)[0]
    assert row["provider_category"] == "date_properties"
    assert row["provider_operation"] == "insertDate"
    assert row["provider_request_ordinal"] == 2
    assert "SECRET" not in json.dumps(row)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("provider_status", "INVALID_ARGUMENT"),
        ("provider_detail_shape", "bad_request"),
        ("provider_field_evidence", "recognized"),
    ],
)
def test_provider_envelope_fields_are_exact_allowlisted_values(key: str, value: str) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document stage provider boundary http_status=400"
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {key: value, "raw_field": "SECRET"}
    row = MODULE["sanitize"]([record], TASK)[0]
    assert row[key] == value
    assert "SECRET" not in json.dumps(row)
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {key: "SECRET"}
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


@pytest.mark.parametrize(
    "topic",
    [
        "unknown",
        "time_zone",
        "timestamp",
        "locale",
        "date_format",
        "time_format",
        "location",
        "capability",
        "access",
        "ambiguous",
        "SECRET",
    ],
)
def test_provider_message_topic_has_a_finite_reader_allowlist(topic: str) -> None:
    record = _record()
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {"provider_message_topic": topic}
    if topic == "SECRET":
        with pytest.raises(ValueError, match=r"^$"):
            MODULE["sanitize"]([record], TASK)
    else:
        assert MODULE["sanitize"]([record], TASK)[0]["provider_message_topic"] == topic


@pytest.mark.parametrize(
    "outcome", ["matched", "ambiguous", "oversized", "quoted_lead", "unrecognized_lead"]
)
def test_message_classifier_outcome_has_only_finite_reader_values(outcome: str) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = (
        "document stage provider boundary http_status=400 SECRET"
    )
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {"provider_message_outcome": outcome}
    rows = MODULE["sanitize"]([record], TASK)
    assert rows[0]["provider_message_outcome"] == outcome
    assert "SECRET" not in json.dumps(rows)


@pytest.mark.parametrize(
    "outcome",
    ["SECRET", None, 400, {"SECRET": "SECRET"}],
    ids=["unknown", "null", "numeric", "object"],
)
def test_message_classifier_outcome_rejects_arbitrary_reader_values(outcome: object) -> None:
    record = _record()
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {"provider_message_outcome": outcome}
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


@pytest.mark.parametrize(
    "field",
    [
        "timestamp",
        "timeZoneId",
        "locale",
        "dateFormat",
        "timeFormat",
        "index",
        "tabId",
        "segmentId",
        "startIndex",
        "endIndex",
    ],
)
def test_provider_field_reader_emits_only_validated_static_names(field: str) -> None:
    record = _record()
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {"provider_field": field}
    assert MODULE["sanitize"]([record], TASK)[0]["provider_field"] == field


@pytest.mark.parametrize("field", ["SECRET", "requests[2].insertDate.locale", None, 400])
def test_provider_field_reader_rejects_private_or_malformed_values(field: object) -> None:
    record = _record()
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {"provider_field": field}
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


def test_bullet_stage_snapshot_has_only_finite_labels_and_bounded_counts() -> None:
    record = _record()
    extra = record["jsonPayload"]["record"]["extra"]
    record["jsonPayload"]["record"]["message"] = "document bullet stage snapshot stage=SECRET"
    extra["bullet_stage"] = "pre_style"
    extra["diagnostics"] = {
        "bullet_snapshot_complete": True,
        "bullet_snapshot_underline_false": 10,
        "bullet_snapshot_SECRET_false": 1,
    }
    row = MODULE["sanitize"]([record], TASK)[0]
    assert row["bullet_stage"] == "pre_style"
    assert row["bullet_snapshot_underline_false"] == 10
    assert row["bullet_snapshot_complete"] is True
    assert "SECRET" not in json.dumps(row)
    extra["bullet_stage"] = "SECRET"
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)
    extra["bullet_stage"] = "pre_style"
    extra["diagnostics"]["bullet_snapshot_underline_false"] = 50_001
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("provider_category", "SECRET"),
        ("provider_operation", "SECRET"),
        ("provider_request_ordinal", True),
        ("provider_request_ordinal", 201),
    ],
)
def test_malformed_provider_diagnostics_fail_closed(key: str, value: object) -> None:
    record = _record()
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {key: value}
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)


def test_layout_eligibility_diagnostics_use_fixed_typed_allowlist() -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document tab structure eligibility"
    record["jsonPayload"]["record"]["extra"]["checks"] = {
        "custom_header_footer_margins_present": True,
        "custom_header_footer_margins_enabled": False,
        "custom_header_footer_margins_malformed": False,
        "document_segment_reference_count": 1,
        "unknown_header_footer_style_count": 2,
        "privateFutureFooterField": "private-content",
    }
    row = MODULE["sanitize"]([record], TASK)[0]
    assert row["custom_header_footer_margins_present"] is True
    assert row["custom_header_footer_margins_enabled"] is False
    assert row["custom_header_footer_margins_malformed"] is False
    assert row["document_segment_reference_count"] == 1
    assert row["unknown_header_footer_style_count"] == 2
    assert "private" not in json.dumps(row)


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


@pytest.mark.parametrize(
    "error_code",
    [
        "destination_access_changed",
        "in_place_verification_failed",
        "brand_asset_unavailable",
        "document_transform_internal_error",
    ],
)
def test_emitted_transform_failure_codes_are_diagnostic_sources(error_code: str) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = (
        f"document transform interrupted task_id={TASK} attempt=1"
    )
    record["jsonPayload"]["record"]["extra"]["error_code"] = error_code

    row = MODULE["sanitize"]([record], TASK)[0]

    assert row["error_code"] == error_code


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


@pytest.fixture
def diagnostics_cli(tmp_path: Path) -> Path:
    script = tmp_path / "read-transform-diagnostics.py"
    script.write_bytes((ROOT / "scripts/github/read-transform-diagnostics.py").read_bytes())
    gcloud = tmp_path / "gcloud"
    gcloud.write_text('#!/bin/sh\n: > "$0.called"\nexit 99\n')
    gcloud.chmod(0o700)
    return script


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        pytest.param(["--validate-only"], 0, id="preauth-validation"),
        pytest.param(["--help"], 1, id="help-rejected"),
        pytest.param(["--unexpected=SECRET"], 1, id="unknown-rejected"),
    ],
)
def test_diagnostics_cli_validation_and_argument_errors_do_not_read_logs(
    diagnostics_cli: Path, arguments: list[str], expected: int
) -> None:
    result = subprocess.run(
        [sys.executable, str(diagnostics_cli), *arguments],
        env={"QA_TASK_ID": TASK, "PATH": str(diagnostics_cli.parent)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected
    assert not result.stdout
    assert result.stderr == (
        "" if expected == 0 else "Diagnostic read rejected or unavailable; no raw logs emitted.\n"
    )
    assert "SECRET" not in result.stderr
    assert not (diagnostics_cli.parent / "gcloud.called").exists()


@pytest.mark.parametrize(
    "task_id",
    [
        pytest.param("", id="missing"),
        pytest.param("SECRET", id="untrusted"),
        pytest.param(TASK.upper(), id="noncanonical"),
    ],
)
def test_diagnostics_cli_rejects_invalid_task_before_authentication_or_log_reads(
    diagnostics_cli: Path, task_id: str
) -> None:
    result = subprocess.run(
        [sys.executable, str(diagnostics_cli), "--validate-only"],
        env={"QA_TASK_ID": task_id, "PATH": str(diagnostics_cli.parent)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert not result.stdout
    assert result.stderr == "Diagnostic read rejected or unavailable; no raw logs emitted.\n"
    assert not (diagnostics_cli.parent / "gcloud.called").exists()


def test_workflow_uses_main_only_existing_wif_and_no_input_shell_interpolation() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/read-transform-diagnostics.yml").read_text()
    )
    job = workflow["jobs"]["diagnostics"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["env"] == {"QA_TASK_ID": "${{ inputs.task_id }}"}
    steps = job["steps"]
    assert steps[0]["with"]["persist-credentials"] is False
    setup = next(step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@"))
    validate = next(
        step
        for step in steps
        if step.get("name") == "Validate exact task UUID before authentication"
    )
    authenticate = next(
        step for step in steps if step.get("uses", "").startswith("google-github-actions/auth@")
    )
    read = next(
        step for step in steps if step.get("name") == "Read only bounded allowlisted diagnostics"
    )
    assert setup["with"]["version"] == "0.13.5"
    assert validate["run"] == (
        "uv run --script scripts/github/read-transform-diagnostics.py --validate-only"
    )
    assert read["run"] == "uv run --script scripts/github/read-transform-diagnostics.py"
    assert (
        steps.index(setup) < steps.index(validate) < steps.index(authenticate) < steps.index(read)
    )
    assert authenticate["with"] == {
        "workload_identity_provider": "${{ secrets.WIF_PROVIDER }}",
        "service_account": "${{ secrets.WIF_SERVICE_ACCOUNT }}",
    }
    assert all("${{" not in step.get("run", "") for step in steps)
