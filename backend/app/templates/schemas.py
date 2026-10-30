from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.identifiers import DriveId, FileId


TemplateMimeType = Literal[
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.presentation",
]


class TemplateRead(BaseModel):
    file_id: FileId
    drive_id: DriveId
    drive_name: str
    name: str
    description: str | None
    mime_type: TemplateMimeType
    modified_time: str
    category_path: list[str]


class TemplateCatalogRead(BaseModel):
    templates: list[TemplateRead]
