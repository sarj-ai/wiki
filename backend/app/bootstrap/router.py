from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.bootstrap.dependencies import bootstrap_service_dep
from app.bootstrap.schemas import BootstrapRead
from app.bootstrap.service import BootstrapService


router = APIRouter(tags=["bootstrap"])


@router.get("/bootstrap")
async def bootstrap(
    svc: Annotated[BootstrapService, Depends(bootstrap_service_dep)],
) -> BootstrapRead:
    return await svc.load()
