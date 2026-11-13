from typing import Annotated, Literal, Protocol, assert_never

from pydantic import ConfigDict, Field, JsonValue, NonNegativeInt

from app.document_transform.errors import SourceChangedError, UnsupportedSourceError
from app.document_transform.gateway import NativeDocumentSnapshot, SourceSnapshot
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    DriveFileSource,
    StrictTransformModel,
)
from app.identifiers import DocsRevisionId, FileId, UserId
from app.tasks.errors import TaskNotClaimableError, TaskNotFoundError
from app.tasks.schemas import (
    ActiveDocumentTransformTaskRecord,
    DocumentTransformTaskRecord,
    ReviewDocumentTransformTaskRecord,
    TerminalDocumentTransformTaskRecord,
)


class NativeSourceReader(Protocol):
    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot: ...


class _NativeDocumentSnapshot(StrictTransformModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    account_email: str
    file_id: FileId
    revision_id: DocsRevisionId
    document: dict[str, JsonValue]


class NativeDocumentInspection(_NativeDocumentSnapshot):
    kind: Literal["native"] = "native"


class SavedNativeDocumentInspection(_NativeDocumentSnapshot):
    kind: Literal["saved-task"] = "saved-task"
    checkpoint: DocumentTransformCheckpoint | None
    checkpoint_generation: NonNegativeInt


type NativeDocumentInspectionRead = Annotated[
    NativeDocumentInspection | SavedNativeDocumentInspection, Field(discriminator="kind")
]


async def inspect_native_document(
    source: DriveFileSource, *, reader: NativeSourceReader, account_email: str
) -> NativeDocumentInspection:
    # The reader uses the same Wiki user and file-scope checks as transformation.
    snapshot = await reader.load_drive_source(source)
    if snapshot.modified_time != source.expected_modified_time:
        raise SourceChangedError
    if not isinstance(snapshot, NativeDocumentSnapshot):
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
) -> SavedNativeDocumentInspection:
    if record.owner_user_id != owner_user_id or record.task.resource_id != inspection.file_id:
        raise TaskNotFoundError
    match record:
        case ActiveDocumentTransformTaskRecord() | TerminalDocumentTransformTaskRecord():
            return SavedNativeDocumentInspection(
                account_email=inspection.account_email,
                file_id=inspection.file_id,
                revision_id=inspection.revision_id,
                document=inspection.document,
                checkpoint=record.checkpoint,
                checkpoint_generation=record.checkpoint_generation,
            )
        case ReviewDocumentTransformTaskRecord():
            raise TaskNotClaimableError
        case _:
            assert_never(record)
