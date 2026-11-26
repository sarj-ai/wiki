from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Final
import uuid

from fastapi import HTTPException
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.document_transform.schemas import DocumentIdPolishRequest
from app.files.errors import FilesError
from app.identifiers import IdempotencyKey
from app.mcp.context import (
    ContextFactory,
    McpApplicationContext,
    McpAuthorizationError,
    authorized_mcp_context,
)
from app.mcp.mutation import polish_document_by_id
from app.mcp.schemas import (
    AdmissionInput,
    DocumentIdPolishResponse,
    McpCapability,
    McpResponse,
    TaskInput,
    TaskResponse,
)
from app.mcp.service import McpReadService
from app.tasks.errors import TaskError
from app.tasks.queue import TaskEnqueuer


REST_PREFIX = "/mcp/v1"
_HEADERS: Final[dict[str, str]] = {"Cache-Control": "private, no-store"}
_MAX_BODY_BYTES = 16_384
type _Operation = Callable[[McpApplicationContext], Awaitable[McpResponse]]


def _error(status: int, code: str, *, authenticate: str | None = None) -> JSONResponse:
    headers = dict(_HEADERS)
    if authenticate is not None:
        headers["WWW-Authenticate"] = authenticate
    return JSONResponse(
        {"code": code, "detail": "Wiki request could not be completed"},
        status_code=status,
        headers=headers,
    )


def _authentication_required(resource_metadata_url: str | None) -> JSONResponse:
    challenge = "Bearer"
    if resource_metadata_url is not None:
        challenge += f' resource_metadata="{resource_metadata_url}"'
    return _error(401, "authentication_required", authenticate=challenge)


def _success(result: McpResponse) -> JSONResponse:
    headers = dict(_HEADERS)
    status = 200
    match result:
        case DocumentIdPolishResponse():
            status = 202
            headers.update(
                {
                    "Idempotency-Key": str(result.idempotency_key),
                    "Idempotency-Replayed": str(result.replayed).lower(),
                    "Location": f"{REST_PREFIX}/tasks/{result.task.id}",
                }
            )
        case _:
            pass
    if isinstance(result, TaskResponse) and result.retry_after_seconds is not None:
        headers["Retry-After"] = str(result.retry_after_seconds)
    return JSONResponse(result.model_dump(mode="json"), status_code=status, headers=headers)


def register_rest_routes(
    server: FastMCP,
    *,
    context_factory: ContextFactory,
    enqueuer: TaskEnqueuer,
    request_timeout: timedelta,
    resource_metadata_url: str | None,
) -> None:
    async def execute(
        operation: _Operation, capability: McpCapability = McpCapability.READ
    ) -> JSONResponse:
        if get_access_token() is None:
            return _authentication_required(resource_metadata_url)
        try:
            async with authorized_mcp_context(
                context_factory, capability, request_timeout=request_timeout
            ) as context:
                result = await operation(context)
            return _success(result)
        except McpAuthorizationError:
            return _error(
                403,
                "insufficient_scope",
                authenticate='Bearer error="insufficient_scope", scope="wiki:read wiki:transform"',
            )
        except TaskError as error:
            return _error(error.http_status, error.code)
        except FilesError as error:
            return _error(404 if error.http_status in {403, 404} else 502, "document_unavailable")
        except HTTPException as error:
            return _error(error.status_code, "polish_request_rejected")
        except TimeoutError:
            return _error(504, "request_timeout")
        except BaseException as error:
            if not isinstance(error, Exception):
                raise
            return _error(502, "wiki_request_failed")

    @server.custom_route(f"{REST_PREFIX}/polish", methods=["POST"])
    async def polish(request: Request) -> JSONResponse:
        # Bearer-only: browser session cookies never authorize this API.
        if get_access_token() is None:
            return _authentication_required(resource_metadata_url)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            return _error(415, "json_required")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > _MAX_BODY_BYTES:
                return _error(413, "request_too_large")
            body.extend(chunk)
        try:
            payload = DocumentIdPolishRequest.model_validate_json(body)
            if (header := request.headers.get("Idempotency-Key")) is not None:
                key = IdempotencyKey(uuid.UUID(header))
                if "idempotency_key" in payload.model_fields_set and payload.idempotency_key != key:
                    return _error(422, "idempotency_key_mismatch")
                payload = payload.model_copy(update={"idempotency_key": key})
        except ValidationError, ValueError:
            return _error(422, "invalid_polish_request")

        async def operation(context: McpApplicationContext) -> McpResponse:
            return await polish_document_by_id(payload, context=context, enqueuer=enqueuer)

        return await execute(operation, McpCapability.TRANSFORM)

    @server.custom_route(f"{REST_PREFIX}/tasks/{{task_id}}", methods=["GET"])
    async def task(request: Request) -> JSONResponse:
        if get_access_token() is None:
            return _authentication_required(resource_metadata_url)
        try:
            payload = TaskInput(task_id=request.path_params["task_id"])
        except ValidationError:
            return _error(422, "invalid_task_id")

        async def operation(context: McpApplicationContext) -> McpResponse:
            return await McpReadService(context).get_task(payload)

        return await execute(operation)

    @server.custom_route(f"{REST_PREFIX}/admissions/{{idempotency_key}}", methods=["GET"])
    async def admission(request: Request) -> JSONResponse:
        if get_access_token() is None:
            return _authentication_required(resource_metadata_url)
        try:
            payload = AdmissionInput(idempotency_key=request.path_params["idempotency_key"])
        except ValidationError:
            return _error(422, "invalid_idempotency_key")

        async def operation(context: McpApplicationContext) -> McpResponse:
            return await McpReadService(context).get_admission(payload)

        return await execute(operation)
