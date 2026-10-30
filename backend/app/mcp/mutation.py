from app.document_transform.application import submit_document_transform
from app.mcp.context import McpApplicationContext
from app.mcp.schemas import (
    MarkdownImportInput,
    McpCapability,
    PolishInput,
    PolishResponse,
    TaskInput,
)
from app.mcp.service import McpReadService
from app.tasks.queue import TaskEnqueuer


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
    )
    # Replays can already be terminal: use the same owner/output-ACL read boundary.
    public = await McpReadService(context).get_task(TaskInput(task_id=submission.task.id))
    return PolishResponse(
        task=public.task,
        retry_after_seconds=public.retry_after_seconds,
        replayed=submission.replayed,
    )
