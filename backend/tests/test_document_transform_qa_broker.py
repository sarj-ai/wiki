from datetime import UTC, datetime
import uuid

import pytest

from app.document_transform.qa_broker import plan_qa_canary
from app.document_transform.qa_grants import QaAuthority, QaGrantError
from app.document_transform.schemas import OutputStrategy, TransformMode
from app.drive.schemas import MIME_DOC, MIME_FOLDER, DriveNode
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId, UserId
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId


_SOURCE = FileId("source-folder")
_DESTINATION = FileId("destination-folder")
_DRIVE = DriveId("personal-drive")


def _authority() -> QaAuthority:
    return QaAuthority(
        grant_id=DocumentTransformQaGrantId(uuid.UUID("11111111-1111-4111-8111-111111111111")),
        user_id=UserId(uuid.UUID("22222222-2222-4222-8222-222222222222")),
        user_email="qa@example.test",
        execution_name="job/executions/one",
        release_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        source_folder_id=_SOURCE,
        destination_folder_id=_DESTINATION,
        mode=TransformMode.BOTH,
        expires_at=datetime(2026, 9, 7, 12, tzinfo=UTC),
        job_expires_at=datetime(2026, 9, 7, 13, tzinfo=UTC),
    )


class _Files:
    async def get(self, file_id: FileId) -> FileResponse:
        if file_id == _SOURCE:
            return FileResponse(
                id=file_id,
                name="Source",
                mime_type=MIME_FOLDER,
                parent_folder_id=None,
                drive_id=_DRIVE,
                modified_time=None,
            )
        return FileResponse(
            id=file_id,
            name="Destination",
            mime_type=MIME_FOLDER,
            parent_folder_id=None,
            drive_id=_DRIVE,
            modified_time=None,
            can_add_children=True,
        )


class _Drive:
    def __init__(self, children: dict[FileId, list[DriveNode]]) -> None:
        self._children = children

    async def list_folder_children_bounded(
        self,
        folder_id: FileId,
        *,
        max_pages: int,
        max_children: int,
    ) -> list[DriveNode]:
        assert max_pages > 0
        assert max_children > 0
        return self._children.get(folder_id, [])


def _node(
    file_id: str,
    *,
    mime_type: str = MIME_DOC,
    parent_id: FileId = _SOURCE,
    modified_time: str | None = "2026-09-07T12:00:00Z",
) -> DriveNode:
    return DriveNode(
        id=FileId(file_id),
        name=f"Document {file_id}",
        mime_type=mime_type,
        parent_id=parent_id,
        modified_time=modified_time,
    )


async def test_plan_selects_only_server_discovered_doc_and_forces_copy() -> None:
    authority = _authority()
    drive = _Drive({_SOURCE: [_node("doc-1")]})

    first = await plan_qa_canary(authority, drive=drive, files=_Files())
    replay = await plan_qa_canary(authority, drive=drive, files=_Files())

    assert first == replay
    assert first.source_file_id == "doc-1"
    assert first.source_modified_time == "2026-09-07T12:00:00Z"
    assert first.output_strategy is OutputStrategy.COPY
    assert first.plan_id == first.idempotency_key


@pytest.mark.parametrize(
    "children",
    [
        [],
        [_node("doc-1"), _node("doc-2")],
        [_node("doc-1", modified_time=None)],
        [_node("loop", mime_type=MIME_FOLDER, parent_id=_SOURCE)],
    ],
    ids=("empty", "ambiguous", "missing-revision", "cycle"),
)
async def test_plan_rejects_ambiguous_incomplete_or_cyclic_source(
    children: list[DriveNode],
) -> None:
    drive = _Drive(
        {
            _SOURCE: children,
            FileId("loop"): [_node("loop", mime_type=MIME_FOLDER, parent_id=FileId("loop"))],
        }
    )

    with pytest.raises(QaGrantError):
        await plan_qa_canary(_authority(), drive=drive, files=_Files())


async def test_plan_key_changes_when_source_version_changes() -> None:
    first = await plan_qa_canary(
        _authority(), drive=_Drive({_SOURCE: [_node("doc-1")]}), files=_Files()
    )
    changed = await plan_qa_canary(
        _authority(),
        drive=_Drive({_SOURCE: [_node("doc-1", modified_time="2026-09-07T12:01:00Z")]}),
        files=_Files(),
    )

    assert first.plan_id != changed.plan_id
