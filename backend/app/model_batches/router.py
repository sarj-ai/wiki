from __future__ import annotations

from typing import Annotated, assert_never

from fastapi import APIRouter, Depends, HTTPException, Response, status
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.model_batches.dependencies import model_batch_coordinator_dep
from app.model_batches.gateway import job_id_from_output_object
from app.model_batches.service import (
    JobSettled,
    JobStillRunning,
    JobUnknown,
    ModelBatchCoordinator,
    ReconcileReport,
)
from app.tasks.internal_auth import require_model_batch_event


class _StorageNotification(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    event_type: Annotated[str, Field(alias="eventType")]
    object_id: Annotated[str, Field(alias="objectId", min_length=1, max_length=1_024)]


class _PushMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    attributes: _StorageNotification
    message_id: Annotated[str, Field(alias="messageId")]


class PubSubPushEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    message: _PushMessage
    subscription: str


async def receive_model_batch_event(
    envelope: PubSubPushEnvelope,
    coordinator: Annotated[ModelBatchCoordinator, Depends(model_batch_coordinator_dep)],
) -> Response:
    notification = envelope.message.attributes
    job_id = job_id_from_output_object(notification.object_id)
    if notification.event_type != "OBJECT_FINALIZE" or job_id is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    advance = await coordinator.advance(job_id)
    match advance:
        case JobStillRunning():
            # Output can land before the job reports a terminal state; Pub/Sub
            # redelivers with backoff until it settles.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Model batch job is still finishing",
            )
        case JobSettled(task_resumed=resumed):
            logger.info(
                "model batch event handled",
                model_batch_job_id=job_id,
                task_resumed=resumed,
                pubsub_message_id=envelope.message.message_id,
            )
        case JobUnknown():
            logger.warning("model batch event for unknown job", model_batch_job_id=job_id)
        case unreachable:
            assert_never(unreachable)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def reconcile_model_batches(
    coordinator: Annotated[ModelBatchCoordinator, Depends(model_batch_coordinator_dep)],
) -> ReconcileReport:
    return await coordinator.reconcile(limit=20)


class ModelBatchesRouter:
    @staticmethod
    def build() -> APIRouter:
        router = APIRouter(
            prefix="/internal/model-batches",
            tags=["internal-model-batches"],
            include_in_schema=False,
            dependencies=[Depends(require_model_batch_event)],
        )
        router.add_api_route(
            "/events",
            receive_model_batch_event,
            methods=["POST"],
            status_code=status.HTTP_204_NO_CONTENT,
        )
        router.add_api_route("/reconcile", reconcile_model_batches, methods=["POST"])
        return router
