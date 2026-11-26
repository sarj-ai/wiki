from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException

from app.document_transform.application import TransformSubmission, submit_document_transform
from app.document_transform.schemas import (
    DocumentIdPolishRequest,
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
)
from app.drive.schemas import MIME_DOC
from app.drive.service import DriveService
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import FileId, UserId
from app.tasks.queue import TaskEnqueuer
from app.tasks.service import TaskService


@dataclass(frozen=True, slots=True)
class _SourceReader:
    files: FilesService
    source_id: FileId
    direct: bool

    async def get(self, file_id: FileId) -> FileResponse:
        if self.direct and file_id == self.source_id:
            return await self.files.get_direct(file_id)
        return await self.files.get(file_id)


async def submit_document_by_id(
    request: DocumentIdPolishRequest,
    *,
    owner_user_id: UserId,
    files: FilesService,
    drive: DriveService,
    tasks: TaskService,
    enqueuer: TaskEnqueuer,
    in_place_enabled: bool,
) -> TransformSubmission:
    if request.output_strategy is OutputStrategy.IN_PLACE and not in_place_enabled:
        raise HTTPException(status_code=503, detail="In-place document transformation is disabled")
    reader = _SourceReader(
        files=files,
        source_id=request.file_id,
        direct=request.output_strategy is OutputStrategy.COPY,
    )
    source = await reader.get(request.file_id)
    if source.id != request.file_id:
        message = "File not available in Sarj Wiki"
        raise FileNotInScopeError(message)
    if source.mime_type != MIME_DOC:
        raise HTTPException(status_code=422, detail="A native Google Doc is required")
    if source.modified_time is None:
        raise HTTPException(
            status_code=503, detail="The latest document version could not be verified"
        )
    try:
        modified = datetime.fromisoformat(source.modified_time)
    except ValueError as error:
        raise HTTPException(
            status_code=503, detail="The latest document version could not be verified"
        ) from error
    if modified.tzinfo is None or modified.utcoffset() is None:
        raise HTTPException(
            status_code=503, detail="The latest document version could not be verified"
        )
    destination = request.copy_destination_folder_id
    if request.output_strategy is OutputStrategy.COPY and destination is None:
        personal = next((item for item in await drive.list_drives() if item.is_personal), None)
        if personal is None:
            raise HTTPException(
                status_code=503, detail="A writable My Drive destination is required"
            )
        destination = FileId(personal.id)
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file_id",
            file_id=request.file_id,
            expected_modified_time=source.modified_time,
        ),
        mode=request.mode,
        output_strategy=request.output_strategy,
        output_name=request.output_name,
        copy_destination_folder_id=destination,
    )
    return await submit_document_transform(
        payload=payload,
        owner_user_id=owner_user_id,
        idempotency_key=request.idempotency_key,
        tasks=tasks,
        enqueuer=enqueuer,
        files=reader,
        in_place_enabled=in_place_enabled,
    )
