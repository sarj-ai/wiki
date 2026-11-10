from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import JsonValue

from app.document_transform.errors import OutputConflictError, OutputVerificationError
from app.document_transform.readability import verify_readability
from app.document_transform.readability_json import document_digest
from app.document_transform.readability_paragraphs import readable_paragraph_requests
from app.document_transform.readability_tables import readable_table_requests
from app.document_transform.readability_types import ReadabilityCheckpoint, complete_readability
from app.identifiers import DocsRevisionId, FileId


@dataclass(frozen=True, slots=True)
class ReadabilityProvider:
    read: Callable[[FileId], Awaitable[dict[str, JsonValue]]]
    write: Callable[[FileId, ReadabilityCheckpoint], Awaitable[None]]

    async def apply(
        self, *, document_id: FileId, checkpoint: ReadabilityCheckpoint
    ) -> ReadabilityCheckpoint:
        document = await self.read(document_id)
        if verify_readability(document, checkpoint):
            if checkpoint.complete and document.get("revisionId") != checkpoint.revision_id:
                raise OutputConflictError
        else:
            if (
                checkpoint.complete
                or document.get("revisionId") != checkpoint.source_revision_id
                or document_digest(document) != checkpoint.source_sha256
            ):
                raise OutputConflictError
            requests = readable_paragraph_requests(checkpoint.paragraphs) + readable_table_requests(
                document, checkpoint.tables
            )
            if requests != checkpoint.requests:
                raise OutputConflictError
            await self.write(document_id, checkpoint)
            document = await self.read(document_id)
            if not verify_readability(document, checkpoint):
                raise OutputVerificationError
        return complete_readability(checkpoint, DocsRevisionId(str(document["revisionId"])))
