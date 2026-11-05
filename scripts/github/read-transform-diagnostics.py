# /// script
# requires-python = "==3.14.8"
# dependencies = ["typer==0.27.3"]
# ///

from __future__ import annotations

from datetime import datetime
import json
import os
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- fixed read-only gcloud CLI without shell
import sys
from types import MappingProxyType
from typing import Annotated, NamedTuple, TypedDict
import uuid

import typer


MAX_COUNT = 1_000_000
MAX_ROWS = 200
MAX_CORRELATED_REQUESTS = 3
MAX_MODEL_ATTEMPTS = 3
MAX_MODEL_VALIDATION_ISSUES = 5
MAX_MODEL_VALIDATION_LOCATION_PARTS = 4
MIN_HTTP_ERROR_STATUS = 100
MAX_HTTP_ERROR_STATUS = 599
MAX_BYTES = 4 * 1024 * 1024
MAX_BULLET_SNAPSHOT_COUNT = 50_000
MAX_FONT_PROOF_COUNT = 750
FONT_PROOF_COUNT_FIELDS = frozenset({"font_proof_count", "font_proof_verified_count"})
BULLET_SNAPSHOT_FIELDS = frozenset(
    f"bullet_snapshot_{field}_{state}"
    for field in ("bold", "italic", "underline")
    for state in ("true", "false", "missing", "malformed")
)
MAX_PROVIDER_ORDINAL = 200
type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type DiagnosticScalar = str | int | bool
MODEL_IDS = frozenset(
    {
        "gemini-3.1-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
    }
)
MODEL_VALIDATION_FIELDS = frozenset(
    {
        "block_id",
        "equivalent",
        "expected_sha256",
        "patches",
        "rationale",
        "reason",
        "replacement",
        "replacement_sha256",
        "reviews",
        "unknown",
    }
)
MODEL_VALIDATION_ERROR_TYPES = frozenset(
    {
        "bool_parsing",
        "bool_type",
        "extra_forbidden",
        "int_type",
        "json_invalid",
        "list_type",
        "literal_error",
        "missing",
        "model_type",
        "string_pattern_mismatch",
        "string_too_long",
        "string_too_short",
        "string_type",
        "too_long",
        "unknown",
        "value_error",
    }
)


class ModelValidationIssue(TypedDict):
    error_type: str
    location: list[str | int]


type DiagnosticValue = DiagnosticScalar | list[ModelValidationIssue]


CLI = typer.Typer(add_completion=False, add_help_option=False, pretty_exceptions_enable=False)
PROVIDER_FIELDS = MappingProxyType(
    {
        "model_operation": frozenset({"unslop", "format", "review"}),
        "model_error_kind": frozenset({"api_error", "timeout", "invalid_payload"}),
        "model_payload_category": frozenset(
            {"none", "unknown", "empty", "coverage", "binding", "schema"}
        ),
        "model_error_reason": frozenset(
            {
                "unknown",
                "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                "BILLING_DISABLED",
                "CONSUMER_INVALID",
                "IAM_PERMISSION_DENIED",
                "LOCATION_POLICY_VIOLATED",
                "MODEL_NOT_FOUND",
                "PERMISSION_DENIED",
                "RESOURCE_EXHAUSTED",
                "SECURITY_POLICY_VIOLATED",
                "SERVICE_DISABLED",
            }
        ),
        "header_recovery_reason": frozenset(
            {
                "state",
                "ownership",
                "invariants",
                "semantic",
                "bindings",
                "inventory",
                "recovered",
            }
        ),
        "header_recovery_empty_reason": frozenset(
            {
                "segment",
                "content_count",
                "paragraph_wrapper",
                "paragraph_fields",
                "paragraph_style",
                "run_wrapper",
                "text_run",
                "empty",
            }
        ),
        "font_proof_stage": frozenset({"resume", "headers", "content"}),
        "font_proof_reason": frozenset(
            {
                "document_missing",
                "coverage",
                "duplicate_binding",
                "paragraph_payload",
                "parent_styles",
                "malformed",
                "matched",
            }
        ),
        "provider_status": frozenset(
            {
                "INVALID_ARGUMENT",
                "FAILED_PRECONDITION",
                "UNIMPLEMENTED",
                "PERMISSION_DENIED",
                "unknown",
            }
        ),
        "provider_detail_shape": frozenset(
            {"absent", "mixed", "bad_request", "error_info", "other", "malformed"}
        ),
        "provider_field_evidence": frozenset({"none", "recognized", "unrecognized", "conflicting"}),
        "provider_message_outcome": frozenset(
            {"matched", "ambiguous", "oversized", "quoted_lead", "unrecognized_lead"}
        ),
        "safety_stage": frozenset(
            {
                "footer_plan",
                "footer_create",
                "footer_populate",
                "footer_advance",
                "footer_coverage",
                "footer_projection_empty",
                "footer_projection_populated",
                "footer_tab",
                "tab_naming_plan",
                "tab_naming_pre",
                "tab_naming_post",
                "date_plan",
                "date_pre",
            }
        ),
        "safety_reason": frozenset(
            {
                "revision",
                "topology",
                "marker",
                "segment",
                "shape",
                "invariant",
                "action",
                "preimage",
            }
        ),
        "footer_shape_check": frozenset(
            {
                "footer_container",
                "footer_fields",
                "content",
                "paragraph_count",
                "paragraph_container",
                "paragraph_fields",
                "runs",
                "run_container",
                "run_fields",
                "text_run",
                "text_run_fields",
                "text_content",
                "caption",
                "text_style",
                "link",
                "font_family",
                "font_size",
                "bold",
                "italic",
                "alignment",
                "named_style",
            }
        ),
        "footer_shape_reason": frozenset({"missing", "mismatch"}),
        "provider_message_topic": frozenset(
            {
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
            }
        ),
        "provider_field": frozenset(
            {
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
            }
        ),
        "bullet_stage": frozenset(
            {
                "source_read",
                "draft_read",
                "pre_header",
                "post_header",
                "pre_style",
                "post_style",
            }
        ),
        "provider_category": frozenset(
            {
                "unknown",
                "date_properties",
                "date_location",
                "delete_range",
                "marker_range",
            }
        ),
        "provider_operation": frozenset({"insertDate", "deleteContentRange", "createNamedRange"}),
    }
)
READ_FAILURES = (ValueError, TypeError, KeyError, OSError, subprocess.TimeoutExpired)
_FAILURE_MESSAGE = "Diagnostic read rejected or unavailable; no raw logs emitted.\n"
BOOLEAN_FIELDS = frozenset(
    [
        "header_recovery_paragraph_mapping",
        "header_recovery_paragraph_direction_present",
        "header_recovery_paragraph_direction_ltr",
        "header_recovery_paragraph_direction_rtl",
        "header_recovery_paragraph_lineSpacing_present",
        "header_recovery_paragraph_lineSpacing_zero",
        "header_recovery_paragraph_lineSpacing_hundred",
        "header_recovery_paragraph_unknown_fields",
        "header_recovery_run_mapping",
        "header_recovery_run_unknown_fields",
        "header_recovery_run_content_lf",
        "header_recovery_run_style_present",
        "header_recovery_run_style_empty",
        *[
            f"header_recovery_paragraph_{field}_{suffix}"
            for field in ("namedStyleType", "alignment")
            for suffix in ("present", "default")
        ],
        *[
            f"header_recovery_paragraph_{field}_{suffix}"
            for field in (
                "spaceAbove",
                "spaceBelow",
                "indentStart",
                "indentEnd",
                "indentFirstLine",
            )
            for suffix in ("present", "unit_only", "explicit_zero")
        ],
        *[
            f"header_recovery_paragraph_{field}_{suffix}"
            for field in (
                "keepWithNext",
                "keepTogether",
                "avoidWidowAndOrphan",
                "pageBreakBefore",
            )
            for suffix in ("present", "false")
        ],
        "header_recovery_revision_equal",
        "header_recovery_document_equal",
        "header_recovery_digest_equal",
        "header_recovery_marker_name_equal",
        "header_recovery_marker_range_equal",
        "header_recovery_custom_equal",
        "header_recovery_identity_equal",
        "header_recovery_structure_equal",
        "header_recovery_protected_equal",
        "header_recovery_body_equal",
        "header_recovery_safe_equal",
        "font_proof_valid",
        "font_proof_components_evaluated",
        "font_proof_paragraph_metadata_equal",
        "font_proof_visible_residual_equal",
        "font_proof_terminal_residual_equal",
        "font_proof_terminal_family_representation_equal",
        "font_proof_parent_context_equal",
        "supported_brand_applied",
        "spacer_named_values_first_entry_only",
        "spacer_named_indent_schema_known",
        "spacer_named_indent_all_zero",
        "spacer_named_shading_schema_known",
        "spacer_named_shading_explicit_transparent",
        "spacer_named_border_schema_known",
        "spacer_named_border_all_width_zero",
        "spacer_named_border_all_padding_zero",
        "spacer_named_border_all_transparent",
        "import_brand_requested",
        "import_single_tab",
        "import_block_count",
        "import_margins",
        "import_header_shape",
        "import_header_title_style",
        "import_header_paragraph_style_known",
        "import_header_named_type_exact",
        "import_header_named_identity_known",
        "import_header_alignment_exact",
        "import_header_line_spacing_exact",
        "import_header_space_above_exact",
        "import_header_space_below_exact",
        "import_header_keep_next_exact",
        "import_header_space_above_unit_only",
        "import_header_space_below_unit_only",
        "import_header_keep_next_absent",
        "import_header_first_failed_run_only",
        "import_header_run_style_known",
        "import_header_run_fields_known",
        "import_header_run_font_exact",
        "import_header_run_size_exact",
        "import_header_run_bold_exact",
        "import_header_run_effects_exact",
        "import_header_run_baseline_exact",
        "import_header_run_background_exact",
        "import_header_run_color_exact",
        "import_header_run_family_weight_exact",
        "import_logo_object",
        "import_logo_metadata",
        "import_logo_title_exact",
        "import_logo_description_exact",
        "import_logo_image_properties_valid",
        "import_logo_source_uri_absent",
        "import_logo_source_uri_exact",
        "import_logo_dimensions",
        "import_footer_shape",
        "import_body_style",
        "import_content_preserved",
        "import_structure_preserved",
        "spacer_scope_checked",
        "spacer_named_styles_known",
        "spacer_dependency_identity_known",
        "spacer_used_named_styles_known",
        "spacer_named_indent_present",
        "spacer_named_border_present",
        "spacer_named_shading_present",
        "in_place_active_suggestions",
        "in_place_logo_available",
        "in_place_marker_available",
        "in_place_planner_findings",
        "in_place_unknown_header",
        "in_place_missing_logo",
        "in_place_missing_marker",
        "bullet_snapshot_complete",
        "plan_available",
        "executable_plan",
        "comments_verified_absent",
        "paragraph_tab_stops",
        "paragraph_borders",
        "paragraph_shading",
        "custom_header_footer_margins_present",
        "custom_header_footer_margins_enabled",
        "custom_header_footer_margins_malformed",
        "content_preserved",
        "structure_preserved",
        "brand_applied",
        "structural_inventory_preserved",
        "protected_inventory_preserved",
        "copy_list_identity_equivalent",
        "occupied_headers_preserved",
        "tab_identities_equal",
        "source_list_projection_valid",
        "output_list_projection_valid",
        "protected_links_equal",
        "protected_bullets_equal",
        "protected_lists_equal",
        "protected_inlineObjects_equal",
        "protected_positionedObjects_equal",
        "protected_footnotes_equal",
        "protected_footers_equal",
        "block_count_mismatch",
        "tabs_present",
        "margins",
        "header_shape",
        "header_paragraph_style",
        "header_title",
        "header_text_style",
        "logo_count",
        "logo_asset",
        "paragraph_ranges",
        "paragraph_named_styles",
        "paragraph_layout",
        "paragraph_text_styles",
        "paragraph_font_only_styles",
        "revision_unchanged",
        "source_document_present",
    ]
)
COUNT_FIELDS = frozenset(
    [
        "spacer_planned_above_count",
        "spacer_planned_below_count",
        "spacer_examined_count",
        "spacer_neighbor_style_count",
        "spacer_neighbor_content_count",
        "spacer_indices_count",
        "spacer_container_count",
        "spacer_style_count",
        "spacer_runs_count",
        "spacer_text_run_count",
        "spacer_budget_count",
        "spacer_unused_named_style_count",
        "spacer_named_identity_count",
        "spacer_named_paragraph_fields_count",
        "spacer_named_paragraph_values_count",
        "spacer_named_text_style_count",
        "word_count",
        "h1_count",
        "h2_count",
        "rejected_native_fields_count",
        "document_segment_reference_count",
        "unknown_header_footer_style_count",
        "bullet_presence_mismatch",
        "bullet_identity_mismatch",
        "bullet_nesting_mismatch",
        "bullet_other_property_mismatch",
        "bullet_projection_accepted",
        "bullet_not_editable",
        "bullet_font_mismatch",
        "bullet_size_mismatch",
        "bullet_color_mismatch",
        "bullet_emphasis_mismatch",
        "bullet_link_mismatch",
        "bullet_unknown_style_mismatch",
        "block_text_mismatches",
        "block_kind_mismatches",
        "block_native_content_mismatches",
        "block_native_style_mismatches",
        "block_editability_mismatches",
        "tab_count",
        "unknown_header_count",
        "missing_header_count",
        "paragraph_count",
        "paragraph_font_only_count",
        "entry_count",
        "definition_count",
        "range_count",
        "malformed_definitions",
        "malformed_ranges",
        "matching_name_count",
        "matching_tab_count",
        "start_present_count",
        "end_present_count",
        "matching_start_count",
        "matching_end_count",
        "exact_range_count",
        "attempt",
    ]
)
EMPHASIS_COUNT_FIELDS = frozenset(
    f"bullet_{field}_{dimension}"
    for field in ("bold", "italic", "underline")
    for dimension in (
        "added_true",
        "added_false",
        "removed_true",
        "removed_false",
        "changed",
        "request_present",
        "request_expected_false",
    )
)
COUNT_FIELD_LIMITS = (
    (BULLET_SNAPSHOT_FIELDS, MAX_BULLET_SNAPSHOT_COUNT),
    (FONT_PROOF_COUNT_FIELDS, MAX_FONT_PROOF_COUNT),
    (COUNT_FIELDS, MAX_COUNT),
    (EMPHASIS_COUNT_FIELDS, MAX_COUNT),
)
ERROR_CODES = frozenset(
    [
        "document_transform_failed",
        "source_changed",
        "destination_access_changed",
        "source_too_large",
        "unsupported_source",
        "output_conflict",
        "output_verification_failed",
        "incompatible_pending_output",
        "model_unavailable",
        "document_provider_unavailable",
        "header_conflict",
        "in_place_not_allowed",
        "needs_review",
        "in_place_verification_failed",
        "brand_asset_unavailable",
        "checkpoint_too_large",
        "document_transform_internal_error",
    ]
)
MESSAGE_PREFIXES = (
    "document model attempt failed operation=",
    "document native font proof checks=",
    "document header recovery checks=",
    "document markdown import verification checks=",
    "document native safety stop stage=",
    "document in-place preflight",
    "document bullet stage snapshot stage=",
    "document stage provider boundary http_status=",
    "document tab structure eligibility",
    "document spacer eligibility",
    "document copy verification failed checks=",
    "document brand verification failed checks=",
    "in-place verification failed content_preserved=",
    "document transform started task_id=",
    "document transform interrupted task_id=",
    "document transform succeeded task_id=",
)


def validate_task_id(value: str) -> str:
    if str(uuid.UUID(value)) != value or not re.fullmatch(r"[0-9a-f-]{36}", value):
        raise ValueError
    return value


def sanitize(
    records: object, task_id: str, request_ids: frozenset[str] = frozenset()
) -> list[dict[str, DiagnosticValue]]:
    validate_task_id(task_id)
    if not isinstance(records, list) or len(records) > MAX_ROWS:
        raise ValueError
    return [
        row
        for entry in records
        if (row := _sanitize_record(entry, task_id, request_ids)) is not None
    ]


def _sanitize_record(
    entry: object, task_id: str, request_ids: frozenset[str]
) -> dict[str, DiagnosticValue] | None:
    if not isinstance(entry, dict):
        raise TypeError
    payload = entry.get("jsonPayload", {})
    if not isinstance(payload, dict) or not isinstance(payload.get("record"), dict):
        return None
    record = payload["record"]
    message = record.get("message")
    extra = record.get("extra", {})
    if not isinstance(message, str) or not message.startswith(MESSAGE_PREFIXES):
        return None
    if not isinstance(extra, dict) or (
        extra.get("task_id") != task_id
        and not ("task_id" not in extra and extra.get("request_id") in request_ids)
    ):
        raise ValueError
    if request_ids and extra.get("request_id") not in request_ids:
        raise ValueError
    row = _diagnostic_row(entry.get("timestamp"), message, extra, task_id)
    for group in (
        extra,
        *(extra.get(key, {}) for key in ("checks", "diagnostics", "marker_checks")),
    ):
        _overlay_diagnostic_fields(row, group)
    return row


def _diagnostic_row(
    timestamp: object, message: str, extra: dict[str, JsonValue], task_id: str
) -> dict[str, DiagnosticValue]:
    if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9T:.+Z-]{20,40}", timestamp):
        raise ValueError
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        raise ValueError
    row: dict[str, DiagnosticValue] = {
        "timestamp": parsed.isoformat(),
        "task_id": task_id,
    }
    for status in ("started", "succeeded", "interrupted"):
        if message.startswith(f"document transform {status} task_id="):
            row["status"] = status
    if "request_id" in extra:
        request_id = extra["request_id"]
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{12,32}", request_id):
            raise ValueError
        row["request_id"] = request_id
    if "error_code" in extra:
        row["error_code"] = _safe_enum(extra["error_code"], ERROR_CODES)
    return row


def _overlay_diagnostic_fields(row: dict[str, DiagnosticValue], group: object) -> None:
    if not isinstance(group, dict):
        raise TypeError
    for key, value in group.items():
        if key in PROVIDER_FIELDS:
            row[key] = _safe_enum(value, PROVIDER_FIELDS[key])
        elif key == "model_id":
            row[key] = _safe_model_id(value)
        elif key == "model_validation_issues":
            row[key] = _safe_model_validation_issues(value)
        elif key == "model_attempt":
            row[key] = _safe_integer(value, 1, MAX_MODEL_ATTEMPTS)
        elif key == "model_http_status":
            row[key] = _safe_http_status(value)
        elif key == "provider_request_ordinal":
            row[key] = _safe_integer(value, 0, MAX_PROVIDER_ORDINAL)
        elif key in BOOLEAN_FIELDS:
            row[key] = _safe_boolean(value)
        else:
            limit = _count_limit(key)
            if limit is not None:
                row[key] = _safe_integer(value, 0, limit)


def _count_limit(key: str) -> int | None:
    for fields, limit in COUNT_FIELD_LIMITS:
        if key in fields:
            return limit
    return None


def _safe_enum(value: object, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError
    return value


def _safe_integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError
    return value


def _safe_http_status(value: object) -> int:
    if type(value) is not int or not (
        value == 0 or MIN_HTTP_ERROR_STATUS <= value <= MAX_HTTP_ERROR_STATUS
    ):
        raise ValueError
    return value


def _safe_boolean(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError
    return value


def _safe_model_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError
    return value if value in MODEL_IDS else "unknown"


def _safe_model_validation_issues(value: object) -> list[ModelValidationIssue]:
    if not isinstance(value, list) or len(value) > MAX_MODEL_VALIDATION_ISSUES:
        raise ValueError
    return [_safe_model_validation_issue(issue) for issue in value]


def _safe_model_validation_issue(value: object) -> ModelValidationIssue:
    if not isinstance(value, dict) or set(value) != {"error_type", "location"}:
        raise ValueError
    error_type = value["error_type"]
    location = value["location"]
    if (
        not isinstance(error_type, str)
        or not isinstance(location, list)
        or len(location) > MAX_MODEL_VALIDATION_LOCATION_PARTS
    ):
        raise ValueError
    return {
        "error_type": error_type if error_type in MODEL_VALIDATION_ERROR_TYPES else "unknown",
        "location": [_safe_model_validation_location(part) for part in location],
    }


def _safe_model_validation_location(value: object) -> str | int:
    if type(value) is int and 0 <= value <= MAX_COUNT:
        return value
    if isinstance(value, str):
        return value if value in MODEL_VALIDATION_FIELDS else "unknown"
    raise ValueError


def read_records(clause: str, limit: int) -> object:
    query = f'resource.type="cloud_run_revision" AND resource.labels.service_name="sarj-docs-backend-dammam" AND ({clause})'
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed logging read with validated IDs, no shell
        [  # ruff: ignore[start-process-with-partial-path] -- setup-gcloud installs the approved CLI on PATH
            "gcloud",
            "logging",
            "read",
            query,
            "--project=sarj-docs",
            "--freshness=24h",
            f"--limit={limit}",
            "--format=json(timestamp,jsonPayload.record)",
            "--quiet",
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise ValueError
    return json.loads(result.stdout)


class _TimedRequest(NamedTuple):
    timestamp: datetime
    request_id: str


def select_correlated_request_ids(
    rows: list[dict[str, DiagnosticValue]],
) -> frozenset[str]:
    timeline: list[_TimedRequest] = []
    for row in rows:
        match row:
            case {"timestamp": str(timestamp), "request_id": str(request_id)}:
                timeline.append(_TimedRequest(datetime.fromisoformat(timestamp), request_id))
            case _:
                continue
    newest = dict.fromkeys(item.request_id for item in sorted(timeline, reverse=True))
    return frozenset(tuple(newest)[:MAX_CORRELATED_REQUESTS])


def run_read(*, validate_only: bool = False) -> int:
    task_id = validate_task_id(os.environ.get("QA_TASK_ID", ""))  # ruff: ignore[banned-api] -- pre-auth validation reads only the task ID, not application settings
    if validate_only:
        return 0
    records = read_records(f'jsonPayload.record.extra.task_id="{task_id}"', MAX_ROWS)
    rows = sanitize(records, task_id)
    # Task execution permits ten attempts; correlate only the newest three
    # distinct requests while retaining bounded lifecycle records for this UUID.
    request_ids = select_correlated_request_ids(rows)
    if len(request_ids) > MAX_CORRELATED_REQUESTS or not isinstance(records, list):
        raise ValueError
    remaining = MAX_ROWS - len(records)
    if request_ids and remaining > 0:
        clause = " OR ".join(
            f'jsonPayload.record.extra.request_id="{request_id}"'
            for request_id in sorted(request_ids)
        )
        correlated = read_records(clause, remaining)
        if not isinstance(correlated, list) or len(correlated) > remaining:
            raise ValueError
        rows.extend(sanitize(correlated, task_id, request_ids))
    sys.stdout.write(
        json.dumps({"records": rows, "returned_count": len(rows)}, sort_keys=True) + "\n"
    )
    return 0


def main(*, validate_only: bool = False) -> int:
    try:
        return run_read(validate_only=validate_only)
    except READ_FAILURES:
        # Never echo subprocess output, exceptions, input or log messages.
        sys.stderr.write(_FAILURE_MESSAGE)
        return 1


@CLI.command(add_help_option=False)
def _read_command(*, validate_only: Annotated[bool, typer.Option()] = False) -> None:
    raise SystemExit(main(validate_only=validate_only))


def cli() -> None:
    try:
        CLI(standalone_mode=False)
    except typer.TyperException:
        sys.stderr.write(_FAILURE_MESSAGE)
        raise SystemExit(1) from None


if __name__ == "__main__":
    cli()
