# ruff: file-ignore[implicit-namespace-package] -- standalone repository CLI, not an import package
from __future__ import annotations

from datetime import datetime
import json
import os
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- fixed read-only gcloud CLI without shell
import sys
import uuid


MAX_COUNT = 1_000_000
MAX_ROWS = 200
MAX_ATTEMPTS = 3
MAX_BYTES = 4 * 1024 * 1024
READ_FAILURES = (ValueError, TypeError, KeyError, OSError, subprocess.TimeoutExpired)
BOOLEAN_FIELDS = frozenset(
    [
        "plan_available",
        "executable_plan",
        "comments_verified_absent",
        "paragraph_tab_stops",
        "paragraph_borders",
        "paragraph_shading",
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
        "word_count",
        "h1_count",
        "h2_count",
        "rejected_native_fields_count",
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
ERROR_CODES = frozenset(
    [
        "document_transform_failed",
        "source_changed",
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
        "checkpoint_too_large",
    ]
)
MESSAGE_PREFIXES = (
    "document tab structure eligibility",
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
                if key in BOOLEAN_FIELDS:
                    if type(value) is not bool:
                        raise ValueError
                    row[key] = value
                elif key in COUNT_FIELDS:
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


def run_read() -> int:
    task_id = validate_task_id(os.environ.get("QA_TASK_ID", ""))  # ruff: ignore[banned-api] -- stdlib-only pre-auth CLI validation, no application settings dependencies
    if sys.argv[1:] == ["--validate-only"]:
        return 0
    if sys.argv[1:]:
        raise ValueError
    records = read_records(f'jsonPayload.record.extra.task_id="{task_id}"', MAX_ROWS)
    rows = sanitize(records, task_id)
    request_ids = frozenset(
        row["request_id"] for row in rows if isinstance(row.get("request_id"), str)
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


def main() -> int:
    try:
        return run_read()
    except READ_FAILURES:
        # Never echo subprocess output, exceptions, input or log messages.
        sys.stderr.write("Diagnostic read rejected or unavailable; no raw logs emitted.\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
