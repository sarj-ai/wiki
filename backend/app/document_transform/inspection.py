from typing import Protocol

from pydantic import JsonValue

from app.document_transform.errors import SourceChangedError, UnsupportedSourceError
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import DriveFileSource, StrictTransformModel
from app.identifiers import DocsRevisionId, FileId


class NativeSourceReader(Protocol):
    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot: ...


class NativeDocumentInspection(StrictTransformModel):
    account_email: str
    file_id: FileId
    revision_id: DocsRevisionId
    document: dict[str, JsonValue]


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
