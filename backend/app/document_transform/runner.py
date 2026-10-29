from __future__ import annotations

from typing import Protocol
import uuid

from app.document_transform.schemas import DocumentTransformRequest, DocumentTransformResult


class DocumentTransformRunner(Protocol):
    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> DocumentTransformResult: ...
