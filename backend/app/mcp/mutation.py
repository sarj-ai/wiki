from app.config import document_transform_in_place_enabled
from app.document_transform.application import submit_document_transform
from app.document_transform.by_id import submit_document_by_id
from app.document_transform.schemas import DocumentIdPolishRequest
from app.mcp.context import McpApplicationContext
from app.mcp.schemas import (
    DocumentIdPolishResponse,
    MarkdownImportInput,
    McpCapability,
    PolishInput,
    PolishResponse,
    TaskInput,
)
from app.mcp.service import McpReadService
from app.tasks.queue import TaskEnqueuer


async def polish_document_by_id(
    request: DocumentIdPolishRequest,
    *,
    context: McpApplicationContext,
    enqueuer: TaskEnqueuer,
) -> DocumentIdPolishResponse:
    context.principal.require(McpCapability.READ)
    context.principal.require(McpCapability.TRANSFORM)
    submission = await submit_document_by_id(
        request,
        owner_user_id=context.principal.user_id,
        files=context.files,
        drive=context.drive,
        tasks=context.tasks,
        enqueuer=enqueuer,
        in_place_enabled=document_transform_in_place_enabled(),
    )
    public = await McpReadService(context).get_task(TaskInput(task_id=submission.task.id))
    return DocumentIdPolishResponse(
        task=public.task,
        retry_after_seconds=public.retry_after_seconds,
        replayed=submission.replayed,
        idempotency_key=request.idempotency_key,
    )


async def polish_document(
    request: PolishInput | MarkdownImportInput,
    *,
    context: McpApplicationContext,
    enqueuer: TaskEnqueuer,
) -> PolishResponse:
    context.principal.require(McpCapability.READ)
    context.principal.require(McpCapability.TRANSFORM)
    submission = await submit_document_transform(
        payload=request.to_transform_request(),
        owner_user_id=context.principal.user_id,
        idempotency_key=request.idempotency_key,
        tasks=context.tasks,
        enqueuer=enqueuer,
        files=context.files,
        in_place_enabled=document_transform_in_place_enabled(),
    )
    # Replays can already be terminal: use the same owner/output-ACL read boundary.
    public = await McpReadService(context).get_task(TaskInput(task_id=submission.task.id))
    return PolishResponse(
        task=public.task,
        retry_after_seconds=public.retry_after_seconds,
        replayed=submission.replayed,
    )
