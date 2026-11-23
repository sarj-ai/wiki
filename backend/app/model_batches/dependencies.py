from __future__ import annotations

from functools import cache
from typing import Annotated, assert_never

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.model_batches.gateway import ModelBatchGateway, ModelBatchLocation, VertexModelBatchGateway
from app.model_batches.service import ModelBatchCoordinator, PostgresModelBatchStore
from app.model_batches.transport import (
    BatchModelTransport,
    DocumentModelTransport,
    OnlineModelTransport,
)
from app.runtime_config import (
    BatchModelTransportConfiguration,
    OnlineModelTransportConfiguration,
)
from app.tasks.dependencies import task_enqueuer_dep
from app.tasks.queue import TaskEnqueuer


def _batch_configuration() -> BatchModelTransportConfiguration:
    configuration = settings.DOCUMENT.model_transport
    match configuration:
        case BatchModelTransportConfiguration():
            return configuration
        case OnlineModelTransportConfiguration():
            msg = "Model batch endpoints require TRANSFORM_MODEL_TRANSPORT=batch"
            raise RuntimeError(msg)
        case unreachable:
            assert_never(unreachable)


@cache
def _cached_gateway() -> ModelBatchGateway:
    configuration = _batch_configuration()
    if settings.GOOGLE_CLOUD_PROJECT is None:
        msg = "GOOGLE_CLOUD_PROJECT is required for model batches"
        raise RuntimeError(msg)
    return VertexModelBatchGateway(
        location=ModelBatchLocation(
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=configuration.location,
            bucket=configuration.bucket,
        )
    )


def model_batch_gateway_dep() -> ModelBatchGateway:
    return _cached_gateway()


def model_batch_coordinator_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    gateway: Annotated[ModelBatchGateway, Depends(model_batch_gateway_dep)],
    enqueuer: Annotated[TaskEnqueuer, Depends(task_enqueuer_dep)],
) -> ModelBatchCoordinator:
    return ModelBatchCoordinator(
        store=PostgresModelBatchStore(db), gateway=gateway, enqueuer=enqueuer
    )


async def document_model_transport_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    enqueuer: Annotated[TaskEnqueuer, Depends(task_enqueuer_dep)],
) -> DocumentModelTransport:
    match settings.DOCUMENT.model_transport:
        case OnlineModelTransportConfiguration():
            return OnlineModelTransport()
        case BatchModelTransportConfiguration():
            store = PostgresModelBatchStore(db)
            return BatchModelTransport(
                store=store,
                coordinator=ModelBatchCoordinator(
                    store=store, gateway=_cached_gateway(), enqueuer=enqueuer
                ),
            )
        case unreachable:
            assert_never(unreachable)
