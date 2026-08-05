from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest

from app.identifiers import DriveId, FileId
from app.smart_create.schemas import ProposalDestination
from scripts.evaluate_smart_create import (
    PrivateTokenizer,
    RedactedCase,
    RedactedReport,
    RedactedResponse,
    StrategySummary,
    offline_judge,
    private_document_description,
    select_confidence_threshold,
    select_strategy,
    write_private_report,
)
from scripts.organize_my_drive import CapabilitySnapshot, InventoryItem


def _destination(folder_id: str, path: list[str]) -> ProposalDestination:
    return ProposalDestination(
        folder_id=FileId(folder_id),
        drive_id=DriveId("drive"),
        drive_name="Private drive name",
        folder_path=path,
    )


def _summary(strategy: str, rate: float, *, unsafe: int = 0) -> StrategySummary:
    return StrategySummary(
        strategy=strategy,
        completed=50,
        acceptable=round(rate * 50),
        acceptable_rate=rate,
        safety_failures=unsafe,
        mean_score=rate,
        mean_latency=timedelta(milliseconds=100),
    )


def test_offline_judge_accepts_exact_or_durable_non_root_ancestor() -> None:
    reference = _destination("exact", ["My Drive", "Work", "Roadmaps"])

    assert offline_judge(reference, reference).score == 1
    assert offline_judge(reference, _destination("work", ["My Drive", "Work"])).acceptable
    assert not offline_judge(reference, _destination("root", ["My Drive"])).acceptable
    assert not offline_judge(reference, _destination("other", ["My Drive", "Personal"])).acceptable


def test_private_document_description_uses_title_metadata_and_content() -> None:
    item = InventoryItem(
        id="private-document-id",
        name="Private document title",
        mime_type="application/vnd.google-apps.document",
        parent_ids=["parent"],
        owned_by_me=True,
        shared=False,
        trashed=False,
        modified_time="2026-08-04T12:00:00Z",
        capabilities=CapabilitySnapshot(canMoveItemWithinDrive=True),
        permissions=[],
        permission_fingerprint="fingerprint",
        path="Private/path",
        depth=1,
        root_child_id="parent",
    )

    description = private_document_description(item, "  Content\nexcerpt  ")

    assert "Title: Private document title" in description
    assert "Modified: 2026-08-04T12:00:00Z" in description
    assert "Content excerpt: Content excerpt" in description
    assert len(private_document_description(item, "x" * 3000)) == 2000


def test_strategy_selection_rejects_unsafe_and_prefers_shallower_near_best_lite() -> None:
    selected = select_strategy(
        [
            _summary("gemini-3.5-flash-lite:depth-1", 0.89),
            _summary("gemini-3.5-flash-lite:depth-3", 0.90),
            _summary("gemini-3.5-flash:depth-1", 0.93),
            _summary("gemini-3.5-flash:depth-unlimited", 0.99, unsafe=1),
        ]
    )

    assert selected == "gemini-3.5-flash-lite:depth-1"


def test_threshold_selection_uses_lowest_zero_rejection_threshold() -> None:
    responses = [
        RedactedResponse(
            strategy="candidate",
            destination="folder_token",
            confidence=0.65,
            latency=timedelta(milliseconds=1),
            score=0,
            acceptable=False,
            moved=True,
        ),
        RedactedResponse(
            strategy="candidate",
            destination="folder_token",
            confidence=0.75,
            latency=timedelta(milliseconds=1),
            score=1,
            acceptable=True,
            moved=True,
        ),
    ]

    selection = select_confidence_threshold(responses)

    assert selection.threshold == 0.7
    assert [summary.judge_rejected_moves for summary in selection.summaries] == [1, 0, 0, 0]


@pytest.mark.parametrize(
    "private_value",
    [
        "private-document-id",
        "Sensitive Person",
        "private-folder-id",
        "private model content",
    ],
)
def test_report_does_not_persist_private_values(tmp_path: Path, private_value: str) -> None:
    tokenizer = PrivateTokenizer(b"fixed-test-key")
    case = RedactedCase(
        document=tokenizer.token("doc", "private-document-id"),
        reference_destination=tokenizer.token("folder", "private-folder-id"),
        best_strategy=None,
        best_response=None,
        responses=[],
    )
    report = RedactedReport(
        generated_at="2026-08-04T00:00:00+00:00",
        requested_cases=1,
        completed_cases=1,
        strategies=[],
        confidence_thresholds=[],
        selected_strategy=None,
        selected_confidence_threshold=0.9,
        cases=[case],
    )
    path = tmp_path / "private" / "report.json"

    write_private_report(path, report)
    serialized = path.read_text()

    assert private_value not in serialized
    assert json.loads(serialized)["mode"] == "read-only"
    assert path.stat().st_mode & 0o777 == 0o600
