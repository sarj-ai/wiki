from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.drive.schemas import MIME_FOLDER
from app.identifiers import DriveId, FileId
from app.smart_create.schemas import ProposalDestination
from app.smart_create.service import GeneratedReorganization
from scripts.evaluate_reorganization import (
    PrivateTokenizer,
    RedactedReport,
    ReferenceJudgment,
    ResponseJudgment,
    build_personal_tree,
    make_redacted_case,
    write_private_report,
)
from scripts.organize_my_drive import CapabilitySnapshot, InventoryItem


PRIVATE_VALUES = (
    "private-document-id",
    "Sensitive Person Visa Notes",
    "private-current-folder",
    "private-reference-folder",
    "private-best-folder",
    "Private reference reason",
    "Private model reason",
    "Private judge reason",
    "Sensitive Person",
)


def _item(*, item_id: str, name: str, mime_type: str, parents: list[str]) -> InventoryItem:
    return InventoryItem(
        id=item_id,
        name=name,
        mime_type=mime_type,
        parent_ids=parents,
        owned_by_me=True,
        shared=False,
        trashed=False,
        capabilities=CapabilitySnapshot(),
        permissions=[],
        permission_fingerprint="fingerprint",
        path=name,
        depth=1,
        root_child_id=item_id,
    )


def test_personal_tree_normalizes_root_parent() -> None:
    tree = build_personal_tree(
        [
            _item(item_id="root-folder", name="Private", mime_type=MIME_FOLDER, parents=["root"]),
            _item(
                item_id="child-folder",
                name="Nested",
                mime_type=MIME_FOLDER,
                parents=["root-folder"],
            ),
        ],
        "root",
    )

    assert tree.drive_id == DriveId("root")
    assert tree.folders[0].parent_id is None
    assert tree.folders[1].parent_id == FileId("root-folder")


@pytest.mark.parametrize("private_value", PRIVATE_VALUES)
def test_redacted_report_excludes_private_value(tmp_path: Path, private_value: str) -> None:
    item = _item(
        item_id="private-document-id",
        name="Sensitive Person Visa Notes",
        mime_type="application/vnd.google-apps.document",
        parents=["private-current-folder"],
    )
    current = ProposalDestination(
        folder_id=FileId("private-current-folder"),
        drive_id=DriveId("root"),
        drive_name="My Drive",
        folder_path=["My Drive", "Sensitive Person"],
    )
    reference = ReferenceJudgment(
        destination_folder_id=FileId("private-reference-folder"),
        confidence=0.9,
        reason="Private reference reason",
    )
    response = GeneratedReorganization(
        destination_folder_id=FileId("private-best-folder"),
        reason="Private model reason",
    )
    redacted = make_redacted_case(
        tokenizer=PrivateTokenizer(b"fixed-test-key"),
        item=item,
        current=current,
        reference=reference,
        responses=[response],
        judgment=ResponseJudgment(
            best_response_index=0,
            score=0.8,
            acceptable=True,
            reason="Private judge reason",
        ),
        errors=[],
    )
    report = RedactedReport(
        generated_at="2026-08-04T00:00:00+00:00",
        candidate_model="candidate",
        judge_model="judge",
        requested_cases=1,
        responses_per_case=1,
        candidate_concurrency=1,
        completed_cases=1,
        acceptable_cases=1,
        cases=[redacted],
    )
    path = tmp_path / "private" / "report.json"
    write_private_report(path, report)
    serialized = path.read_text()

    assert private_value not in serialized
    assert json.loads(serialized)["mode"] == "read-only"
    assert path.stat().st_mode & 0o777 == 0o600
