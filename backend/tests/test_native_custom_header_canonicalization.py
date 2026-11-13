from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Never, override
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import (
    InPlaceInspection,
    NativeDocumentSnapshot,
    OutputVerification,
    SourceSnapshot,
)
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.native_table_font import TableFontPlan
from app.document_transform.readability_json import array, bodies, obj
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
    TransformMode,
)
from app.document_transform.source_access import NativeSourceAccess, PersonalDrive
from app.files.service import FilesService
from app.identifiers import DocsRevisionId, FileId, TaskId
from app.models.user import User
from tests.test_document_transform import _CheckpointStore, _Gateway, _Generator


class OfflineFiles(FilesService):
    @override
    async def create(self, parent_folder_id: FileId, name: str, mime_type: str) -> Never:
        raise AssertionError

    @override
    async def get(self, file_id: FileId) -> Never:
        raise AssertionError

    @override
    async def can_edit(self, file_id: FileId) -> Never:
        raise AssertionError

    @override
    async def get_html_content(self, file_id: FileId) -> Never:
        raise AssertionError

    @override
    async def rename(self, file_id: FileId, new_name: str) -> Never:
        raise AssertionError

    @override
    async def move(self, file_id: FileId, new_parent_folder_id: FileId) -> Never:
        raise AssertionError

    @override
    async def copy(self, file_id: FileId, new_name: str) -> Never:
        raise AssertionError

    @override
    async def delete(self, file_id: FileId) -> Never:
        raise AssertionError


def _fixture(name: str) -> dict[str, JsonValue]:
    path = Path(__file__).parent / "data" / "native_custom_header_canonicalization" / f"{name}.json"
    return json.loads(path.read_text())


def _tab(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return next(bodies(document))[1]


def _snapshot(document: dict[str, JsonValue]) -> NativeDocumentSnapshot:
    return NativeDocumentSnapshot(
        title=str(document["title"]),
        parent_folder_id=FileId("folder"),
        modified_time="now",
        revision_id=DocsRevisionId(str(document["revisionId"])),
        google_document=document,
        access=NativeSourceAccess(owned_by_me=True, can_edit=True, location=PersonalDrive()),
    )


async def test_unslop_preserves_existing_header_prefix_proof_without_authorizing_header_writes(
    native_header_gateway: GoogleDocumentGateway,
) -> None:
    document = _fixture("after")
    title = str(document["title"])
    inspection = await native_header_gateway.inspect_in_place(
        snapshot=_snapshot(document), title=title
    )
    assert inspection.custom_header_titles_present

    class NativeReadbackGateway(_Gateway):
        @override
        async def inspect_in_place(
            self,
            *,
            snapshot: SourceSnapshot,
            title: str,
            table_font_plan: TableFontPlan | None = None,
        ) -> InPlaceInspection:
            return await native_header_gateway.inspect_in_place(
                snapshot=snapshot, title=title, table_font_plan=table_font_plan
            )

        @override
        async def verify_in_place(self, **kwargs: Any) -> OutputVerification:
            return await native_header_gateway.verify_in_place(**kwargs)

    gateway = NativeReadbackGateway(_snapshot(document))
    store = _CheckpointStore([])
    await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=TaskId(uuid.uuid7()),
        lease_token=uuid.uuid7(),
    ).execute(
        operation_id=uuid.uuid7(),
        request=DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file",
                file_id=FileId(str(document["documentId"])),
                expected_modified_time="now",
            ),
            mode=TransformMode.UNSLOP,
            output_strategy=OutputStrategy.IN_PLACE,
        ),
    )
    checkpoint = store.checkpoints[-1]
    assert checkpoint.custom_header_titles == inspection.custom_header_titles_present
    assert not checkpoint.custom_header_title_targets
    assert not checkpoint.custom_header_titles_required
    verified = await native_header_gateway.verify_in_place(
        document=document,
        title=title,
        expected=parse_google_document(document, title=title),
        structural_sha256=checkpoint.structural_sha256,
        protected_structure_sha256=checkpoint.protected_structure_sha256,
        verify_brand=False,
        custom_furniture=checkpoint.custom_furniture,
        custom_header_titles=checkpoint.custom_header_titles,
    )
    assert verified.content_preserved
    assert verified.structure_preserved


@pytest.fixture
def native_header_gateway() -> GoogleDocumentGateway:
    return GoogleDocumentGateway(
        user=User(id=uuid.uuid7(), email="qa@example.test"),
        files=OfflineFiles(),
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
    )


async def test_captured_google_header_coalescing_retains_exact_effective_suffix_style(
    native_header_gateway: GoogleDocumentGateway,
) -> None:
    before, after = _fixture("before"), _fixture("after")
    verified = await _verify_header_preservation(native_header_gateway, before, after)
    assert verified.content_preserved
    assert verified.structure_preserved


async def _verify_header_preservation(
    gateway: GoogleDocumentGateway,
    before: dict[str, JsonValue],
    after: dict[str, JsonValue],
) -> OutputVerification:
    title = str(before["title"])
    inspection = await gateway.inspect_in_place(snapshot=_snapshot(before), title=title)
    assert inspection.custom_furniture
    return await gateway.verify_in_place(
        document=after,
        title=title,
        expected=parse_google_document(before, title=title),
        structural_sha256=inspection.structural_sha256,
        protected_structure_sha256=inspection.protected_structure_sha256,
        verify_brand=False,
        custom_furniture=inspection.custom_furniture,
        custom_header_titles=inspection.custom_header_title_targets,
    )


async def test_reopened_native_header_recognizes_the_inserted_title_without_duplication(
    native_header_gateway: GoogleDocumentGateway,
) -> None:
    before, after = _fixture("before"), _fixture("after")
    title = str(before["title"])
    initial = await native_header_gateway.inspect_in_place(snapshot=_snapshot(before), title=title)
    reopened = await native_header_gateway.inspect_in_place(snapshot=_snapshot(after), title=title)
    assert initial.custom_furniture
    assert reopened.custom_furniture == initial.custom_furniture
    assert len(reopened.custom_header_titles_present) == 1
    assert not reopened.custom_header_title_targets


@pytest.mark.parametrize(
    "change", ["text", "font", "bold", "link", "unknown", "parent", "footer", "image"]
)
async def test_effective_header_equivalence_still_rejects_meaningful_native_changes(
    change: str,
    native_header_gateway: GoogleDocumentGateway,
) -> None:
    before, after = _fixture("before"), deepcopy(_fixture("after"))
    tab = _tab(after)
    header = obj(obj(tab["headers"])["kix.hf0"])
    paragraph = obj(obj(array(header["content"])[0])["paragraph"])
    runs = [
        obj(item["textRun"])
        for value in array(paragraph["elements"])
        if "textRun" in (item := obj(value))
    ]
    run = next(item for item in runs if "X" in str(item.get("content", "")))
    style = obj(run["textStyle"])
    match change:
        case "text":
            run["content"] = str(run["content"]).replace("X", "Y", 1)
        case "font":
            style["weightedFontFamily"] = {"fontFamily": "Arial"}
        case "bold":
            style["bold"] = False
        case "link":
            style["link"] = {"url": "https://example.test/changed"}
        case "unknown":
            style["unknownStyle"] = "changed"
        case "parent":
            parent = obj(array(obj(tab["namedStyles"])["styles"])[0])
            obj(parent["textStyle"])["italic"] = True
        case "footer":
            tab["footers"] = {}
        case _:
            image = obj(next(iter(obj(tab["inlineObjects"]).values())))
            obj(obj(image["inlineObjectProperties"])["embeddedObject"])["title"] = "Changed logo"
    verified = await _verify_header_preservation(native_header_gateway, before, after)
    assert not verified.structure_preserved
