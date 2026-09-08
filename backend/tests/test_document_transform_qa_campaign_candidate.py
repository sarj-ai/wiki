from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest

from app.document_transform.qa_broker import plan_qa_corpus_campaign
from app.document_transform.qa_grants import QaAuthority, QaGrantError
from app.document_transform.schemas import TransformMode
from app.document_transform.word_gateway import WORD_MIME
from app.drive.schemas import (
    MIME_DOC,
    MIME_FOLDER,
    BoundedFolderMembers,
    DriveNode,
    DrivePermission,
    DrivePermissionDetail,
    FilePermissionSnapshot,
)
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId, UserId
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId


_SOURCE = FileId("security-folder")
_SOURCE_DRIVE = DriveId("security-drive")
_DESTINATION = FileId("qa-destination")


def _authority(*, mode: TransformMode = TransformMode.FORMAT) -> QaAuthority:
    return QaAuthority(
        grant_id=DocumentTransformQaGrantId(uuid.UUID("11111111-1111-4111-8111-111111111111")),
        user_id=UserId(uuid.UUID("22222222-2222-4222-8222-222222222222")),
        user_email="qa@example.test",
        execution_name="projects/test/locations/test/executions/one",
        release_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        source_folder_id=_SOURCE,
        destination_folder_id=_DESTINATION,
        mode=mode,
        expires_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
        job_expires_at=datetime(2026, 9, 8, 13, tzinfo=UTC),
    )


def _members(*, docx: bool = False, unsupported: bool = False) -> list[DriveNode]:
    members = [
        DriveNode(
            id=FileId(f"document-{ordinal}"),
            name=f"Security Document {ordinal}",
            mime_type=MIME_DOC,
            parent_id=_SOURCE,
            drive_id=_SOURCE_DRIVE,
            modified_time="2026-09-08T10:00:00Z",
            version="1",
        )
        for ordinal in range(20)
    ]
    if docx:
        members[0] = members[0].model_copy(
            update={"name": "Security Document 0.docx", "mime_type": WORD_MIME}
        )
    if unsupported:
        members[0] = members[0].model_copy(update={"mime_type": "application/pdf"})
    return members


def _owner_permission(*, version: str = "17", shared: bool = False) -> FilePermissionSnapshot:
    return FilePermissionSnapshot(
        file_id=_DESTINATION,
        mime_type=MIME_FOLDER,
        drive_id=None,
        parent_ids=(),
        version=version,
        owned_by_me=True,
        shared=shared,
        can_add_children=True,
        owner_emails=("qa@example.test",),
        permissions=(
            DrivePermission(
                id="owner-permission",
                principal_type="user",
                role="owner",
                email_address="qa@example.test",
                inherited=False,
                pending_owner=False,
                deleted=False,
                permission_details=(
                    DrivePermissionDetail(
                        permission_type="file",
                        role="owner",
                        inherited=False,
                    ),
                ),
            ),
        ),
    )


class _Drive:
    def __init__(
        self,
        members: list[DriveNode],
        *,
        permission: FilePermissionSnapshot | None = None,
    ) -> None:
        self.members = members
        self.permission = permission or _owner_permission()
        self.permission_reads = 0

    async def list_folder_members_bounded(
        self,
        folder_id: FileId,
        *,
        drive_id: DriveId,
        max_pages: int,
        max_children: int,
        max_metadata_bytes: int,
    ) -> BoundedFolderMembers:
        if folder_id == _DESTINATION:
            return BoundedFolderMembers(members=[], page_count=1, metadata_bytes=2)
        assert folder_id == _SOURCE
        assert drive_id == _SOURCE_DRIVE
        assert max_pages > 0
        assert max_children >= 20
        assert max_metadata_bytes > 0
        return BoundedFolderMembers(
            members=self.members,
            page_count=1,
            metadata_bytes=sum(len(member.model_dump_json().encode()) for member in self.members),
        )

    async def read_file_permissions_bounded(
        self,
        file_id: FileId,
        *,
        max_pages: int,
        max_permissions: int,
    ) -> FilePermissionSnapshot:
        assert file_id == _DESTINATION
        assert max_pages > 0
        assert max_permissions > 0
        self.permission_reads += 1
        return self.permission


class _Files:
    def __init__(self, members: list[DriveNode], *, destination_version: str = "17") -> None:
        self.member_responses = {
            member.id: FileResponse(
                id=member.id,
                name=member.name,
                mime_type=member.mime_type,
                parent_folder_id=member.parent_id,
                drive_id=member.drive_id,
                modified_time=member.modified_time,
                version=member.version,
            )
            for member in members
        }
        self.destination_version = destination_version

    async def get(self, file_id: FileId) -> FileResponse:
        if file_id == _SOURCE:
            return FileResponse(
                id=_SOURCE,
                name="Security",
                mime_type=MIME_FOLDER,
                parent_folder_id=None,
                drive_id=_SOURCE_DRIVE,
                modified_time=None,
                version="root-1",
            )
        assert file_id == _DESTINATION
        return FileResponse(
            id=_DESTINATION,
            name="QA Output",
            mime_type=MIME_FOLDER,
            parent_folder_id=None,
            drive_id=None,
            modified_time=None,
            version=self.destination_version,
            can_add_children=True,
        )

    async def get_qa_corpus_metadata_many(
        self, file_ids: list[FileId]
    ) -> dict[FileId, FileResponse]:
        return {file_id: self.member_responses[file_id] for file_id in file_ids}


def _twenty_one_members() -> list[DriveNode]:
    members = _members()
    return [*members, members[0].model_copy(update={"id": FileId("document-20")})]


def _members_with_nested_destination() -> list[DriveNode]:
    return [
        *_members(),
        DriveNode(
            id=_DESTINATION,
            name="Unsafe Nested Output",
            mime_type=MIME_FOLDER,
            parent_id=_SOURCE,
            drive_id=_SOURCE_DRIVE,
            modified_time="2026-09-08T10:00:00Z",
            version="17",
        ),
    ]


async def test_campaign_candidate_binds_exact_twenty_and_owner_only_destination() -> None:
    members = _members()
    first = await plan_qa_corpus_campaign(
        _authority(),
        drive=_Drive(members),
        files=_Files(members),
    )
    second_drive = _Drive(list(reversed(members)))
    second = await plan_qa_corpus_campaign(
        _authority(),
        drive=second_drive,
        files=_Files(members),
    )

    assert first.plan == second.plan
    assert first.corpus_plan_sha256 == second.corpus_plan_sha256
    assert first.source_root_sha256 == second.source_root_sha256
    assert first.destination_identity_sha256 == second.destination_identity_sha256
    assert first.destination.sha256 == second.destination.sha256
    assert first.destination.version == second.destination.version
    assert len(first.plan.documents) == 20
    assert not first.plan.unsupported_files
    assert first.destination.file_id == _DESTINATION
    assert first.destination.version == "17"
    for digest in (
        first.corpus_plan_sha256,
        first.source_root_sha256,
        first.destination_identity_sha256,
        first.destination.sha256,
    ):
        assert len(digest) == 64
    assert second_drive.permission_reads == 1


@pytest.mark.parametrize(
    ("members", "mode"),
    [
        (_members(unsupported=True), TransformMode.FORMAT),
        (_members(docx=True), TransformMode.BOTH),
        (_members()[:19], TransformMode.FORMAT),
        (_twenty_one_members(), TransformMode.FORMAT),
    ],
)
async def test_campaign_candidate_rejects_incomplete_or_incompatible_corpus(
    members: list[DriveNode], mode: TransformMode
) -> None:
    drive = _Drive(members)
    with pytest.raises(QaGrantError):
        await plan_qa_corpus_campaign(
            _authority(mode=mode),
            drive=drive,
            files=_Files(members),
        )
    assert drive.permission_reads == 0


async def test_campaign_candidate_rejects_destination_nested_in_source() -> None:
    members = _members_with_nested_destination()
    drive = _Drive(members)
    with pytest.raises(QaGrantError):
        await plan_qa_corpus_campaign(
            _authority(),
            drive=drive,
            files=_Files(members),
        )
    assert drive.permission_reads == 0


@pytest.mark.parametrize(
    ("permission", "destination_version"),
    [
        (_owner_permission(shared=True), "17"),
        (_owner_permission(version="18"), "17"),
    ],
)
async def test_campaign_candidate_rejects_non_owner_or_split_destination_snapshot(
    permission: FilePermissionSnapshot, destination_version: str
) -> None:
    members = _members()
    with pytest.raises(QaGrantError):
        await plan_qa_corpus_campaign(
            _authority(),
            drive=_Drive(members, permission=permission),
            files=_Files(members, destination_version=destination_version),
        )


async def test_campaign_candidate_hashes_change_only_for_changed_evidence() -> None:
    members = _members()
    original = await plan_qa_corpus_campaign(
        _authority(),
        drive=_Drive(members, permission=_owner_permission(version="17")),
        files=_Files(members, destination_version="17"),
    )
    changed = await plan_qa_corpus_campaign(
        _authority(),
        drive=_Drive(members, permission=_owner_permission(version="18")),
        files=_Files(members, destination_version="18"),
    )

    assert changed.corpus_plan_sha256 == original.corpus_plan_sha256
    assert changed.source_root_sha256 == original.source_root_sha256
    assert changed.destination_identity_sha256 == original.destination_identity_sha256
    assert changed.destination.sha256 != original.destination.sha256
    assert changed.destination.version != original.destination.version
