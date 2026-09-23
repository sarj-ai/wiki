from __future__ import annotations

import asyncio
from typing import override
import uuid

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import OutputDraft, SourceSnapshot
from app.document_transform.gcs_uploads import load_markdown_upload
from app.document_transform.ir import DocumentIR, rewrite_markdown
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    MarkdownSource,
    TransformMode,
)


class ThreadedMarkdownExecutor(DocumentTransformExecutor):
    upload_owner: uuid.UUID | None = None

    @override
    async def execute(
        self, *, operation_id: uuid.UUID, request: DocumentTransformRequest
    ) -> DocumentTransformResult:
        if (
            isinstance(request.source, MarkdownSource)
            and request.source.kind == "uploaded_markdown"
        ):
            if self.upload_owner is None:
                msg = "Uploaded Markdown requires a task owner"
                raise ValueError(msg)
            markdown = await load_markdown_upload(self.upload_owner, request.source)
            request = request.model_copy(update={"source": markdown})
        return await super().execute(operation_id=operation_id, request=request)

    @override
    async def _create_output(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        source_sha256: str,
        original: DocumentIR,
        transformed: DocumentIR,
        output_name: str | None = None,
    ) -> OutputDraft:
        if snapshot.markdown is None or output_name is None:
            return await super()._create_output(
                operation_id=operation_id,
                request=request,
                snapshot=snapshot,
                source_sha256=source_sha256,
                original=original,
                transformed=transformed,
                output_name=output_name,
            )

        imported_docx = await asyncio.to_thread(
            _render_rewritten_markdown,
            snapshot,
            original,
            transformed,
            output_name=output_name,
            logo_bytes=self._logo_bytes,
            branded=request.mode is not TransformMode.UNSLOP,
        )
        if isinstance(request.source, DriveFileSource):
            return await self._gateway.create_from_drive_markdown(
                operation_id=operation_id,
                source=request.source,
                source_sha256=source_sha256,
                output_name=output_name,
                parent_folder_id=snapshot.parent_folder_id,
                imported_docx=imported_docx,
            )
        return await self._gateway.create_from_markdown(
            operation_id=operation_id,
            source=request.source,
            source_sha256=source_sha256,
            output_name=output_name,
            imported_docx=imported_docx,
        )


def _render_rewritten_markdown(
    snapshot: SourceSnapshot,
    original: DocumentIR,
    transformed: DocumentIR,
    *,
    output_name: str,
    logo_bytes: bytes | None,
    branded: bool,
) -> bytes:
    if snapshot.markdown is None:
        msg = "Markdown source is required for DOCX rendering"
        raise ValueError(msg)
    return render_markdown_docx(
        rewrite_markdown(snapshot.markdown, original, transformed),
        title=snapshot.title,
        header_title=output_name,
        logo_bytes=logo_bytes,
        branded=branded,
    )
