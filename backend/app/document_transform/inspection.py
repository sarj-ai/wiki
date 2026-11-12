from typing import Protocol

from pydantic import JsonValue

from app.document_transform.errors import SourceChangedError, UnsupportedSourceError
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    DriveFileSource,
    StrictTransformModel,
)
from app.identifiers import DocsRevisionId, FileId, UserId
from app.tasks.errors import TaskNotFoundError
from app.tasks.schemas import DocumentTransformTaskRecord


class NativeSourceReader(Protocol):
    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot: ...


class NativeDocumentInspection(StrictTransformModel):
    account_email: str
    file_id: FileId
    revision_id: DocsRevisionId
    document: dict[str, JsonValue]
    checkpoint: DocumentTransformCheckpoint | None = None
    checkpoint_generation: int | None = None


async def inspect_native_document(
    source: DriveFileSource, *, reader: NativeSourceReader, account_email: str
) -> NativeDocumentInspection:
    # The reader uses the same Wiki user and file-scope checks as transformation.
    snapshot = await reader.load_drive_source(source)
    if snapshot.modified_time != source.expected_modified_time:
        raise SourceChangedError
    if snapshot.google_document is None or snapshot.revision_id is None:
        raise UnsupportedSourceError
    parse_google_document(snapshot.google_document, title=snapshot.title)
    return NativeDocumentInspection(
        account_email=account_email,
        file_id=source.file_id,
        revision_id=snapshot.revision_id,
        document=snapshot.google_document,
    )


def attach_saved_checkpoint(
    inspection: NativeDocumentInspection,
    record: DocumentTransformTaskRecord,
    *,
    owner_user_id: UserId,
) -> NativeDocumentInspection:
    if record.owner_user_id != owner_user_id or record.task.resource_id != inspection.file_id:
        raise TaskNotFoundError
    return inspection.model_copy(
        update={
            "checkpoint": record.checkpoint,
            "checkpoint_generation": record.checkpoint_generation,
        }
    )
