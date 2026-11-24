from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass

from pydantic import JsonValue

from app.document_transform.errors import (
    OutputConflictError,
    OutputVerificationError,
    PostprocessingSkippedError,
)
from app.document_transform.postprocessing_recovery import (
    NativeDocumentRollback,
    restore_verified_document,
)
from app.document_transform.readability import verify_readability
from app.document_transform.readability_json import document_digest
from app.document_transform.readability_lists import validate_readable_lists
from app.document_transform.readability_paragraphs import readable_paragraph_requests
from app.document_transform.readability_tables import readable_table_requests
from app.document_transform.readability_terminal import (
    validated_readable_terminal_requests,
)
from app.document_transform.readability_types import (
    ReadabilityCheckpoint,
    complete_readability,
)
from app.identifiers import DocsRevisionId, FileId


@dataclass(frozen=True, slots=True)
class ReadabilityProvider:
    read: Callable[[FileId], Awaitable[dict[str, JsonValue]]]
    write: Callable[[FileId, ReadabilityCheckpoint], Awaitable[None]]
    rollback: NativeDocumentRollback | None = None

    async def apply(
        self, *, document_id: FileId, checkpoint: ReadabilityCheckpoint
    ) -> ReadabilityCheckpoint:
        document = await self.read(document_id)
        if verify_readability(document, checkpoint):
            if (checkpoint.complete and document.get("revisionId") != checkpoint.revision_id) or (
                not checkpoint.requests
                and document.get("revisionId") != checkpoint.source_revision_id
            ):
                raise OutputConflictError
        else:
            if (
                checkpoint.complete
                or document.get("revisionId") != checkpoint.source_revision_id
                or document_digest(document) != checkpoint.source_sha256
            ):
                raise OutputConflictError
            try:
                validate_readable_lists(document, checkpoint.paragraphs)
                requests = (
                    readable_paragraph_requests(
                        checkpoint.paragraphs, grouped_lists=checkpoint.version != 1
                    )
                    + readable_table_requests(
                        document, checkpoint.tables, version=checkpoint.version
                    )
                    + validated_readable_terminal_requests(document, checkpoint)
                )
            except (KeyError, TypeError, ValueError) as error:
                raise OutputConflictError from error
            if requests != checkpoint.requests:
                raise OutputConflictError
            rollback = self.rollback
            if rollback is None:
                raise PostprocessingSkippedError
            before = deepcopy(document)
            await self.write(document_id, checkpoint)
            document = await self.read(document_id)
            if not verify_readability(document, checkpoint, diagnostics=True):
                await restore_verified_document(
                    document_id=document_id,
                    before=before,
                    observed=document,
                    read=self.read,
                    rollback=rollback,
                )
                raise OutputVerificationError
        return complete_readability(checkpoint, DocsRevisionId(str(document["revisionId"])))
