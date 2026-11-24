from collections.abc import Awaitable, Callable
from copy import deepcopy

from pydantic import JsonValue

from app.document_transform.errors import OutputVerificationError
from app.document_transform.readability_json import document_digest
from app.identifiers import FileId


type NativeDocument = dict[str, JsonValue]
type NativeDocumentRead = Callable[[FileId], Awaitable[NativeDocument]]
# A provider may opt in only when it can fence its rejected revision and restore
# the complete preimage without overwriting another editor's changes.
type NativeDocumentRollback = Callable[[FileId, NativeDocument, NativeDocument], Awaitable[None]]


async def restore_verified_document(
    *,
    document_id: FileId,
    before: NativeDocument,
    observed: NativeDocument,
    read: NativeDocumentRead,
    rollback: NativeDocumentRollback,
) -> None:
    expected_sha256 = document_digest(before)
    await rollback(document_id, deepcopy(before), observed)
    restored = await read(document_id)
    if document_digest(restored) != expected_sha256:
        raise OutputVerificationError
