from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.openapi import authentication_responses
from app.templates.dependencies import template_catalog_service_dep
from app.templates.schemas import TemplateCatalogRead
from app.templates.service import TemplateCatalogService


router = APIRouter(
    prefix="/templates",
    tags=["templates"],
    responses=authentication_responses(),
)


@router.get("", status_code=status.HTTP_200_OK, operation_id="list_templates")
async def list_templates(
    templates: Annotated[TemplateCatalogService, Depends(template_catalog_service_dep)],
    *,
    refresh: Annotated[bool, Query()] = False,
) -> TemplateCatalogRead:
    return await templates.list_templates(refresh=refresh)
