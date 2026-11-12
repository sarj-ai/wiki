from dataclasses import asdict, dataclass
import hashlib
from io import BytesIO
import json
import uuid

from docx import Document
import pytest

from app.document_transform.docx_renderer import markdown_import_manifest, render_markdown_docx
from app.document_transform.errors import OutputConflictError
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    PlannedCopyCheckpoint,
    TransformMode,
)
from app.document_transform.threaded_markdown_executor import ThreadedMarkdownExecutor
from app.identifiers import FileId, TaskId
from tests.test_document_transform import _CopyCheckpointStore, _Gateway, _Generator


_SOURCE = "The QA cache retains requests until shutdown."


@dataclass(frozen=True, slots=True)
class LegacyPlanFixture:
    request: DocumentTransformRequest
    checkpoint: PlannedCopyCheckpoint


@pytest.fixture
async def legacy_plan() -> LegacyPlanFixture:
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown", content=_SOURCE, name="QA note", destination_folder_id=FileId("folder")
        ),
        mode=TransformMode.FORMAT,
    )
    store = _CopyCheckpointStore()
    task = TaskId(uuid.uuid7())
    await DocumentTransformExecutor(
        gateway=_Gateway(),
        generator=_Generator(),
        checkpoint_store=store,
        task_id=task,
        lease_token=uuid.uuid7(),
    ).execute(operation_id=task, request=request)
    checkpoint = store.checkpoints[0]
    payload = checkpoint.model_dump(mode="json")
    payload.pop("compact_markdown")
    # Keep current source/model bindings; seed only the old public renderer
    # manifest so this tests the missing-policy schema rather than policy drift.
    manifest = markdown_import_manifest(
        _SOURCE, title="QA note", branded=True, header_title=checkpoint.output_name
    )
    encoded = json.dumps(
        asdict(manifest), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    payload["manifest_sha256"] = hashlib.sha256(encoded).hexdigest()
    return LegacyPlanFixture(request, PlannedCopyCheckpoint.model_validate(payload))


@pytest.mark.parametrize("branded", [True, False])
@pytest.mark.parametrize("source", [_SOURCE, "中文：保留全部数字。", "احفظ المعرف `QA-001`."])
def test_compact_paragraph_keeps_body_and_metadata_without_inventing_a_title(
    source: str, *, branded: bool
) -> None:
    data = render_markdown_docx(
        source, title="File metadata", branded=branded, compact_single_paragraph=True
    )
    document = Document(BytesIO(data))
    manifest = markdown_import_manifest(
        source, title="File metadata", branded=branded, compact_single_paragraph=True
    )

    assert document.core_properties.title == "File metadata"
    assert [paragraph.text for paragraph in document.paragraphs] == [manifest.blocks[0].text]
    assert [block.kind for block in manifest.blocks] == ["paragraph"]
    assert document.paragraphs[0].style is not None
    assert document.paragraphs[0].style.name is not None
    assert "Title" not in document.paragraphs[0].style.name


@pytest.mark.parametrize(
    ("source", "body"),
    [
        ("# Authored title\n\nBody", ["Authored title", "Body"]),
        (
            "First paragraph.\n\nSecond paragraph.",
            ["File metadata", "First paragraph.", "Second paragraph."],
        ),
        ("```python\nprint('literal')\n```", ["File metadata", "print('literal')"]),
    ],
)
def test_compact_policy_preserves_authored_titles_and_other_document_forms(
    source: str, body: list[str]
) -> None:
    document = Document(
        BytesIO(render_markdown_docx(source, title="File metadata", compact_single_paragraph=True))
    )

    assert [paragraph.text for paragraph in document.paragraphs] == body


@pytest.mark.parametrize("executor_type", [DocumentTransformExecutor, ThreadedMarkdownExecutor])
@pytest.mark.parametrize("mode", list(TransformMode))
async def test_new_copy_persists_compact_policy_in_both_rendering_paths(
    executor_type: type[DocumentTransformExecutor], mode: TransformMode
) -> None:
    gateway = _Gateway()
    store = _CopyCheckpointStore()
    task = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown", content=_SOURCE, name="QA note", destination_folder_id=FileId("folder")
        ),
        mode=mode,
    )

    await executor_type(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=task,
        lease_token=uuid.uuid7(),
    ).execute(operation_id=task, request=request)

    assert store.checkpoints
    assert all(checkpoint.compact_markdown for checkpoint in store.checkpoints)
    assert gateway.imported_docx is not None
    document = Document(BytesIO(gateway.imported_docx))
    assert document.core_properties.title == "QA note"
    assert [paragraph.text for paragraph in document.paragraphs] == [_SOURCE]


@pytest.mark.parametrize("executor_type", [DocumentTransformExecutor, ThreadedMarkdownExecutor])
async def test_plan_without_compact_policy_resumes_its_legacy_title_manifest(
    executor_type: type[DocumentTransformExecutor],
    legacy_plan: LegacyPlanFixture,
) -> None:
    request, checkpoint = legacy_plan.request, legacy_plan.checkpoint
    assert not checkpoint.compact_markdown
    gateway = _Gateway()
    store = _CopyCheckpointStore()
    task = TaskId(uuid.uuid7())

    await executor_type(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=task,
        lease_token=uuid.uuid7(),
        checkpoint=checkpoint,
    ).execute(operation_id=task, request=request)

    assert not any(value.compact_markdown for value in store.checkpoints)
    assert gateway.imported_docx is not None
    document = Document(BytesIO(gateway.imported_docx))
    assert [paragraph.text for paragraph in document.paragraphs] == ["QA note", _SOURCE]


async def test_changing_saved_title_policy_fails_manifest_binding_before_creation(
    legacy_plan: LegacyPlanFixture,
) -> None:
    request, checkpoint = legacy_plan.request, legacy_plan.checkpoint
    checkpoint = checkpoint.model_copy(update={"compact_markdown": True})
    gateway = _Gateway()
    task = TaskId(uuid.uuid7())

    with pytest.raises(OutputConflictError):
        await DocumentTransformExecutor(
            gateway=gateway,
            generator=_Generator(),
            checkpoint_store=_CopyCheckpointStore(),
            task_id=task,
            lease_token=uuid.uuid7(),
            checkpoint=checkpoint,
        ).execute(operation_id=task, request=request)

    assert gateway.imported_docx is None
    assert gateway.applies == 0
