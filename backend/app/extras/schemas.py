from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, StringConstraints


IconStr = Annotated[str, StringConstraints(min_length=1, max_length=8)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class DocIconWrite(BaseModel):
    icon: IconStr | None = None
    cover_url: HttpUrl | None = None


class DocIconRead(BaseModel):
    drive_id: str
    icon: str | None
    cover_url: str | None


class FolderIconWrite(BaseModel):
    icon: IconStr | None = None


class FolderIconRead(BaseModel):
    folder_drive_id: str
    icon: str | None


class SortOrderWrite(BaseModel):
    drive_id: str
    sort_order: FiniteFloat


class SortOrderBulkWrite(BaseModel):
    items: list[SortOrderWrite] = []


class SortOrderRead(BaseModel):
    drive_id: str
    sort_order: FiniteFloat


class SortOrderBulkResult(BaseModel):
    updated: int


class PreferencesWrite(BaseModel):
    show_non_doc_files: bool | None = None
    hidden_workspace_ids: list[str] | None = None


class PreferencesRead(BaseModel):
    show_non_doc_files: bool
    hidden_workspace_ids: list[str]
