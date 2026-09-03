from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.bootstrap.dependencies import bootstrap_service_dep
from app.bootstrap.schemas import BootstrapRead
from app.bootstrap.service import BootstrapService
from app.openapi import authentication_responses


router = APIRouter(tags=["bootstrap"], responses=authentication_responses())


@router.get(
    "/bootstrap",
    status_code=status.HTTP_200_OK,
    operation_id="get_bootstrap",
)
async def bootstrap(
    svc: Annotated[BootstrapService, Depends(bootstrap_service_dep)],
    *,
    defer_doc_icons: Annotated[bool, Query()] = False,
) -> BootstrapRead:
    return await svc.load(defer_doc_icons=defer_doc_icons)
