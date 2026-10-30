from collections.abc import Iterable
from typing import override

import httplib2
import pytest

from app.document_transform import qa_corpus
from app.document_transform.qa_corpus import (
    QaCorpusPlanError,
    UnsupportedCorpusReason,
    plan_qa_corpus,
)
from app.document_transform.word_gateway import WORD_MIME
from app.drive.errors import DriveTraversalIncompleteError
from app.drive.schemas import MIME_DOC, MIME_FOLDER, MIME_SHORTCUT, BoundedFolderMembers, DriveNode
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId


_SOURCE = FileId("security-folder")
_DRIVE = DriveId("security-drive")


def _node(
    value: str,
    *,
    mime_type: str = MIME_DOC,
    name: str | None = None,
    parent_id: FileId = _SOURCE,
    modified_time: str | None = "2026-09-08T12:00:00Z",
    drive_id: DriveId = _DRIVE,
    version: str | None = "1",
) -> DriveNode:
    return DriveNode(
        id=FileId(value),
        name=name or f"Document {value}",
        mime_type=mime_type,
        parent_id=parent_id,
        modified_time=modified_time,
        version=version,
        drive_id=drive_id,
    )


def _response(node: DriveNode) -> FileResponse:
    return FileResponse(
        id=node.id,
        name=node.name,
        mime_type=node.mime_type,
        parent_folder_id=node.parent_id,
        drive_id=_DRIVE,
        modified_time=node.modified_time,
        version=node.version,
    )


def _root(
    *,
    name: str = "Security",
    parent_folder_id: FileId | None = None,
    drive_id: DriveId = _DRIVE,
    modified_time: str | None = None,
    version: str | None = "root-v1",
) -> FileResponse:
    return FileResponse(
        id=_SOURCE,
        name=name,
        mime_type=MIME_FOLDER,
        parent_folder_id=parent_folder_id,
        drive_id=drive_id,
        modified_time=modified_time,
        version=version,
    )


class _Drive:
    def __init__(self, snapshots: Iterable[list[DriveNode] | tuple[list[DriveNode], int]]) -> None:
        self.snapshots = list(snapshots)
        self.calls: list[tuple[FileId, DriveId, int, int, int]] = []

    async def list_folder_members_bounded(
        self,
        folder_id: FileId,
        *,
        drive_id: DriveId,
        max_pages: int,
        max_children: int,
        max_metadata_bytes: int,
    ) -> BoundedFolderMembers:
        self.calls.append((folder_id, drive_id, max_pages, max_children, max_metadata_bytes))
        value = self.snapshots.pop(0)
        members, page_count = value if isinstance(value, tuple) else (value, 1)
        return BoundedFolderMembers(
            members=members,
            page_count=page_count,
            metadata_bytes=sum(len(item.model_dump_json().encode()) for item in members),
        )


class _IncompleteDrive(_Drive):
    @override
    async def list_folder_members_bounded(
        self,
        folder_id: FileId,
        *,
        drive_id: DriveId,
        max_pages: int,
        max_children: int,
        max_metadata_bytes: int,
    ) -> BoundedFolderMembers:
        del folder_id, drive_id, max_pages, max_children, max_metadata_bytes
        raise DriveTraversalIncompleteError


class _Files:
    def __init__(
        self,
        members: Iterable[DriveNode],
        *,
        missing: set[FileId] | None = None,
        replacements: dict[FileId, FileResponse] | None = None,
        roots: Iterable[FileResponse] | None = None,
    ) -> None:
        self.members = {member.id: _response(member) for member in members}
        self.missing = missing or set()
        self.members.update(replacements or {})
        self.batches: list[list[FileId]] = []
        self.root_reads = 0
        self.roots = iter(roots) if roots is not None else None

    async def get(self, file_id: FileId) -> FileResponse:
        assert file_id == _SOURCE
        self.root_reads += 1
        if self.roots is not None:
            return next(self.roots)
        return _root()

    async def get_qa_corpus_metadata_many(
        self, file_ids: list[FileId]
    ) -> dict[FileId, FileResponse]:
        self.batches.append(file_ids)
        return {
            file_id: self.members[file_id] for file_id in file_ids if file_id not in self.missing
        }


class _TransportFailingFiles(_Files):
    def __init__(self, failure: BaseException) -> None:
        super().__init__([])
        self.failure = failure

    @override
    async def get(self, file_id: FileId) -> FileResponse:
        del file_id
        raise self.failure


async def test_plan_includes_every_document_beyond_one_hundred_and_batches_metadata() -> None:
    members = [_node(f"doc-{index:03}") for index in range(205)]
    drive = _Drive([members, list(reversed(members))])
    files = _Files(members)

    plan = await plan_qa_corpus(source_folder_id=_SOURCE, drive=drive, files=files)

    assert len(plan.documents) == 205
    assert plan.observed_item_count == 205
    assert plan.membership_scope == "recursive_descendants"
    assert [len(batch) for batch in files.batches] == [100, 100, 5]
    assert len({document.file_id for document in plan.documents}) == 205
    assert plan.source_folder == qa_corpus.QaCorpusSourceFolder(
        file_id=_SOURCE,
        name="Security",
        mime_type=MIME_FOLDER,
        parent_folder_id=None,
        drive_id=_DRIVE,
        modified_time=None,
        version="root-v1",
    )
    assert [call[:4] for call in drive.calls] == [
        (_SOURCE, _DRIVE, 100, 100_000),
        (_SOURCE, _DRIVE, 99, 100_000),
    ]
    assert drive.calls[0][4] == qa_corpus.QA_MAX_TRAVERSAL_METADATA_BYTES
    assert drive.calls[1][4] < drive.calls[0][4]


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("private timeout detail"),
        ConnectionError("private connection detail"),
        httplib2.ServerNotFoundError("private DNS detail"),
    ],
    ids=("timeout", "connection", "server-not-found"),
)
async def test_plan_normalizes_exhausted_provider_transport_failures(
    failure: BaseException,
) -> None:
    with pytest.raises(QaCorpusPlanError) as raised:
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([]),
            files=_TransportFailingFiles(failure),
        )

    assert not str(raised.value)
    assert raised.value.__cause__ is failure


async def test_plan_preserves_native_docx_mix_and_explicit_unsupported_inventory() -> None:
    members = [
        _node("native"),
        _node("word", mime_type=WORD_MIME, name="Security Review.DOCX"),
        _node("sheet", mime_type="application/vnd.google-apps.spreadsheet"),
        _node("shortcut", mime_type=MIME_SHORTCUT),
        _node("bad-word", mime_type=WORD_MIME, name="extensionless"),
    ]

    plan = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([members, members]),
        files=_Files(members),
    )

    assert [(item.file_id, item.kind, item.supported_modes) for item in plan.documents] == [
        ("native", "native_google_doc", ("format", "unslop", "both")),
        ("word", "docx", ("format",)),
    ]
    assert {item.file_id: item.reason for item in plan.unsupported_files} == {
        "bad-word": UnsupportedCorpusReason.INVALID_DOCX_NAME,
        "sheet": UnsupportedCorpusReason.UNSUPPORTED_MIME_TYPE,
        "shortcut": UnsupportedCorpusReason.SHORTCUT,
    }


async def test_plan_recursively_enumerates_real_folders_without_following_shortcuts() -> None:
    folder = _node("nested", mime_type=MIME_FOLDER)
    root_doc = _node("root-doc")
    nested_doc = _node("nested-doc", parent_id=folder.id)
    shortcut = _node("shortcut", mime_type=MIME_SHORTCUT, parent_id=folder.id)
    all_members = [folder, root_doc, nested_doc, shortcut]
    drive = _Drive(
        [
            [folder, root_doc],
            [nested_doc, shortcut],
            [folder, root_doc],
            [nested_doc, shortcut],
        ]
    )

    plan = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=drive,
        files=_Files(all_members),
    )

    assert [(item.file_id, item.parent_folder_id) for item in plan.documents] == [
        ("nested-doc", "nested"),
        ("root-doc", _SOURCE),
    ]
    assert plan.traversed_folder_count == 2
    assert [item.file_id for item in plan.unsupported_files] == ["shortcut"]
    assert [call[0] for call in drive.calls] == [_SOURCE, "nested", _SOURCE, "nested"]


async def test_plan_preserves_a_deep_exact_parent_chain() -> None:
    first = _node("level-one", mime_type=MIME_FOLDER)
    second = _node("level-two", mime_type=MIME_FOLDER, parent_id=first.id)
    third = _node("level-three", mime_type=MIME_FOLDER, parent_id=second.id)
    document = _node("deep-doc", parent_id=third.id)
    one_pass = [[first], [second], [third], [document]]

    plan = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([*one_pass, *one_pass]),
        files=_Files([first, second, third, document]),
    )

    assert plan.documents[0].file_id == "deep-doc"
    assert plan.documents[0].parent_folder_id == "level-three"
    assert plan.traversed_folder_count == 4


@pytest.mark.parametrize(
    "cycle_id", [_SOURCE, FileId("nested")], ids=("back-to-root", "self-cycle")
)
async def test_plan_rejects_folder_cycles(cycle_id: FileId) -> None:
    folder = _node("nested", mime_type=MIME_FOLDER)
    cycle = _node(str(cycle_id), mime_type=MIME_FOLDER, parent_id=folder.id)
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[folder], [cycle]]),
            files=_Files([folder, cycle]),
        )


async def test_plan_rejects_duplicate_document_reached_through_two_folders() -> None:
    first_folder = _node("first-folder", mime_type=MIME_FOLDER)
    second_folder = _node("second-folder", mime_type=MIME_FOLDER)
    first = _node("same-doc", parent_id=first_folder.id)
    duplicate = _node("same-doc", parent_id=second_folder.id)
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[first_folder, second_folder], [first], [duplicate]]),
            files=_Files([first_folder, second_folder, first]),
        )


async def test_plan_enforces_global_folder_page_and_item_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_folder = _node("first-folder", mime_type=MIME_FOLDER)
    second_folder = _node("second-folder", mime_type=MIME_FOLDER, parent_id=first_folder.id)
    monkeypatch.setattr(qa_corpus, "QA_MAX_TRAVERSED_FOLDERS", 2)
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[first_folder], [second_folder]]),
            files=_Files([first_folder, second_folder]),
        )

    monkeypatch.setattr(qa_corpus, "QA_MAX_FOLDER_LIST_PAGES", 100)
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([([_node("one")], 60), ([_node("one")], 41)]),
            files=_Files([_node("one")]),
        )

    monkeypatch.setattr(qa_corpus, "QA_MAX_FOLDER_LIST_PAGES", 2)
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([([first_folder], 2)]),
            files=_Files([first_folder]),
        )

    monkeypatch.setattr(qa_corpus, "_MAX_CORPUS_ITEMS", 1)
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[first_folder, _node("extra")]]),
            files=_Files([first_folder]),
        )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ([_node("one")], [_node("one"), _node("added")]),
        ([_node("one"), _node("removed")], [_node("one")]),
        ([_node("one")], [_node("one", modified_time="2026-09-08T12:01:00Z")]),
        ([_node("one")], [_node("one", parent_id=FileId("other"))]),
        ([_node("one")], [_node("one", drive_id=DriveId("other-drive"))]),
    ],
    ids=("added", "removed", "edited", "moved", "drive"),
)
async def test_plan_rejects_folder_membership_or_metadata_churn(
    first: list[DriveNode], second: list[DriveNode]
) -> None:
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([first, second]),
            files=_Files(first),
        )


async def test_plan_rejects_duplicate_ids_across_paginated_results() -> None:
    duplicate = _node("duplicate")
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[duplicate, duplicate], [duplicate]]),
            files=_Files([duplicate]),
        )


async def test_plan_rejects_missing_or_moved_batch_metadata() -> None:
    members = [_node("one"), _node("two")]
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([members, members]),
            files=_Files(members, missing={FileId("two")}),
        )
    moved = _response(members[0]).model_copy(
        update={"parent_folder_id": FileId("outside-security")}
    )
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([members, members]),
            files=_Files(members, replacements={FileId("one"): moved}),
        )


async def test_plan_rejects_listed_or_refreshed_descendant_drive_mismatch() -> None:
    wrong_drive = DriveId("outside-drive")
    listed = _node("one", drive_id=wrong_drive)
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[listed]]),
            files=_Files([listed]),
        )

    member = _node("one")
    refreshed = _response(member).model_copy(update={"drive_id": wrong_drive})
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[member], [member]]),
            files=_Files([member], replacements={member.id: refreshed}),
        )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (_root(), _root(name="Security renamed")),
        (_root(), _root(parent_folder_id=FileId("new-parent"))),
        (_root(), _root(drive_id=DriveId("other-drive"))),
        (_root(), _root(modified_time="2026-09-08T12:01:00Z")),
    ],
    ids=("name", "parent", "drive", "modified-time"),
)
async def test_plan_rejects_source_folder_fingerprint_churn(
    before: FileResponse,
    after: FileResponse,
) -> None:
    member = _node("one")
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[member], [member]]),
            files=_Files([member], roots=[before, after]),
        )


async def test_plan_rejects_provider_version_churn_even_when_modified_time_is_stable() -> None:
    before = _node("one", version="7")
    after = _node("one", version="8")
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[before], [after]]),
            files=_Files([before]),
        )

    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[before], [before]]),
            files=_Files(
                [before],
                replacements={before.id: _response(after)},
            ),
        )


async def test_plan_rejects_root_version_churn() -> None:
    member = _node("one")
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[member], [member]]),
            files=_Files(
                [member],
                roots=[_root(version="1"), _root(version="2")],
            ),
        )


async def test_plan_id_is_order_invariant_and_sensitive_to_root_and_member_metadata() -> None:
    first = _node("one")
    second = _node("two")

    base = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([[first, second], [second, first]]),
        files=_Files([first, second]),
    )
    reordered = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([[second, first], [first, second]]),
        files=_Files([second, first]),
    )
    renamed_root = _root(name="Security Program")
    root_changed = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([[first, second], [first, second]]),
        files=_Files([first, second], roots=[renamed_root, renamed_root]),
    )
    changed_member = _node("one", name="Renamed document")
    member_changed = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([[changed_member, second], [second, changed_member]]),
        files=_Files([changed_member, second]),
    )

    assert reordered.plan_id == base.plan_id
    assert root_changed.plan_id != base.plan_id
    assert member_changed.plan_id != base.plan_id

    revised_member = _node("one", modified_time="2026-09-08T13:00:00Z")
    revision_changed = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([[revised_member, second], [second, revised_member]]),
        files=_Files([revised_member, second]),
    )
    assert revision_changed.plan_id != base.plan_id

    version_changed = _node("one", version="2")
    version_plan = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([[version_changed, second], [second, version_changed]]),
        files=_Files([version_changed, second]),
    )
    assert version_plan.plan_id != base.plan_id


async def test_plan_id_is_pagination_invariant() -> None:
    members = [_node("one"), _node("two")]
    single_page = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([members, members]),
        files=_Files(members),
    )
    paginated = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([(members, 2), (list(reversed(members)), 3)]),
        files=_Files(members),
    )

    assert paginated.plan_id == single_page.plan_id


async def test_empty_and_unsupported_only_folders_are_reported_without_inventing_docs() -> None:
    empty = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([[], []]),
        files=_Files([]),
    )
    assert empty.observed_item_count == 0
    assert empty.documents == ()
    assert empty.unsupported_files == ()

    pdf = _node("policy", mime_type="application/pdf", name="Policy.pdf")
    unsupported = await plan_qa_corpus(
        source_folder_id=_SOURCE,
        drive=_Drive([[pdf], [pdf]]),
        files=_Files([pdf]),
    )
    assert unsupported.documents == ()
    assert unsupported.unsupported_files[0].file_id == "policy"


async def test_incomplete_provider_retry_exhaustion_never_returns_partial_plan() -> None:
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_IncompleteDrive([]),
            files=_Files([]),
        )


async def test_supported_document_without_fresh_revision_is_rejected() -> None:
    member = _node("one", modified_time=None)
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[member], [member]]),
            files=_Files([member]),
        )


@pytest.mark.parametrize("missing", ["root", "member"])
async def test_plan_rejects_missing_provider_version(missing: str) -> None:
    member = _node("one", version=None if missing == "member" else "1")
    roots = [_root(version=None), _root(version=None)] if missing == "root" else None
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[member], [member]]),
            files=_Files([member], roots=roots),
        )


async def test_metadata_size_bound_fails_closed_without_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    member = _node("one")
    monkeypatch.setattr(qa_corpus, "QA_MAX_TRAVERSAL_METADATA_BYTES", 1)
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=_Drive([[member], [member]]),
            files=_Files([member]),
        )


async def test_metadata_byte_budget_is_shared_across_both_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    member = _node("one")
    per_snapshot = len(member.model_dump_json().encode())
    refresh_bytes = len(qa_corpus._canonical_json(qa_corpus._file_fingerprint(_response(member))))
    monkeypatch.setattr(
        qa_corpus,
        "QA_MAX_TRAVERSAL_METADATA_BYTES",
        (2 * per_snapshot) + refresh_bytes - 1,
    )
    drive = _Drive([([member], 1), ([member], 1)])
    with pytest.raises(QaCorpusPlanError):
        await plan_qa_corpus(
            source_folder_id=_SOURCE,
            drive=drive,
            files=_Files([member]),
        )
    assert len(drive.calls) == 2
    assert drive.calls[1][4] == per_snapshot - 1
