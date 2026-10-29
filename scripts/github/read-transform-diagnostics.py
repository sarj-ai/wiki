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
from typing import Annotated
import uuid

import typer


MAX_COUNT = 1_000_000
MAX_ROWS = 200
MAX_ATTEMPTS = 3
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
CLI = typer.Typer(add_completion=False, add_help_option=False, pretty_exceptions_enable=False)
PROVIDER_FIELDS = MappingProxyType(
    {
        "header_recovery_reason": frozenset(
            {"state", "ownership", "invariants", "semantic", "bindings", "inventory", "recovered"}
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
            for field in ("spaceAbove", "spaceBelow", "indentStart", "indentEnd", "indentFirstLine")
            for suffix in ("present", "unit_only", "explicit_zero")
        ],
        *[
            f"header_recovery_paragraph_{field}_{suffix}"
            for field in ("keepWithNext", "keepTogether", "avoidWidowAndOrphan", "pageBreakBefore")
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
) -> list[dict[str, object]]:
    validate_task_id(task_id)
    if not isinstance(records, list) or len(records) > MAX_ROWS:
        raise ValueError
    output: list[dict[str, object]] = []
    for entry in records:
        if not isinstance(entry, dict):
            raise TypeError
        payload = entry.get("jsonPayload", {})
        if not isinstance(payload, dict) or not isinstance(payload.get("record"), dict):
            continue
        record = payload["record"]
        message = record.get("message")
        extra = record.get("extra", {})
        if not isinstance(message, str) or not message.startswith(MESSAGE_PREFIXES):
            continue
        if not isinstance(extra, dict) or (
            extra.get("task_id") != task_id
            and not ("task_id" not in extra and extra.get("request_id") in request_ids)
        ):
            raise ValueError
        if request_ids and extra.get("request_id") not in request_ids:
            raise ValueError
        timestamp = entry.get("timestamp")
        if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9T:.+Z-]{20,40}", timestamp):
            raise ValueError
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            raise ValueError
        row: dict[str, object] = {"timestamp": parsed.isoformat(), "task_id": task_id}
        for status in ("started", "succeeded", "interrupted"):
            if message.startswith(f"document transform {status} task_id="):
                row["status"] = status
        if "request_id" in extra:
            request_id = extra["request_id"]
            if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{12,32}", request_id):
                raise ValueError
            row["request_id"] = request_id
        if "error_code" in extra:
            error_code = extra["error_code"]
            if not isinstance(error_code, str) or error_code not in ERROR_CODES:
                raise ValueError
            row["error_code"] = error_code
        for group in (
            extra,
            *(extra.get(key, {}) for key in ("checks", "diagnostics", "marker_checks")),
        ):
            if not isinstance(group, dict):
                raise TypeError
            for key, value in group.items():
                if key in PROVIDER_FIELDS:
                    if not isinstance(value, str) or value not in PROVIDER_FIELDS[key]:
                        raise ValueError
                    row[key] = value
                elif key == "provider_request_ordinal":
                    if type(value) is not int or not 0 <= value <= MAX_PROVIDER_ORDINAL:
                        raise ValueError
                    row[key] = value
                elif key in BOOLEAN_FIELDS:
                    if type(value) is not bool:
                        raise ValueError
                    row[key] = value
                elif key in BULLET_SNAPSHOT_FIELDS:
                    if type(value) is not int or not 0 <= value <= MAX_BULLET_SNAPSHOT_COUNT:
                        raise ValueError
                    row[key] = value
                elif key in FONT_PROOF_COUNT_FIELDS:
                    if type(value) is not int or not 0 <= value <= MAX_FONT_PROOF_COUNT:
                        raise ValueError
                    row[key] = value
                elif key in COUNT_FIELDS or key in EMPHASIS_COUNT_FIELDS:
                    if type(value) is not int or not 0 <= value <= MAX_COUNT:
                        raise ValueError
                    row[key] = value
        output.append(row)
    return output


def read_records(clause: str, limit: int) -> object:
    query = (
        'resource.type="cloud_run_revision" '
        'AND resource.labels.service_name="sarj-docs-backend-dammam" '
        f"AND ({clause})"
    )
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


def run_read(*, validate_only: bool = False) -> int:
    task_id = validate_task_id(os.environ.get("QA_TASK_ID", ""))  # ruff: ignore[banned-api] -- pre-auth validation reads only the task ID, not application settings
    if validate_only:
        return 0
    records = read_records(f'jsonPayload.record.extra.task_id="{task_id}"', MAX_ROWS)
    rows = sanitize(records, task_id)
    request_ids = frozenset(
        request_id for row in rows if isinstance(request_id := row.get("request_id"), str)
    )
    if len(request_ids) > MAX_ATTEMPTS or not isinstance(records, list):
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
