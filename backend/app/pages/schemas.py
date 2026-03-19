from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Sentinel for distinguishing "field not provided" from "set to None"
_UNSET = object()

PageType = Literal["page", "section"]


# ------------------------------------------------------------------
# Request schemas
# ------------------------------------------------------------------

class PageCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    parent_id: UUID | None = None
    workspace_id: UUID | None = None
    type: PageType = "page"
    icon: str | None = Field(default=None, max_length=100)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Title must not be empty")
        return v


class PageUpdate(BaseModel):
    """All fields are optional.

    For ``parent_id`` we need to distinguish three states:
      - field omitted     -> do not touch parent_id
      - field set to null -> move to root (parent_id = None)
      - field set to UUID -> re-parent to that UUID

    We use a sentinel default so the service layer can tell "omitted" apart
    from "explicitly null".
    """

    title: str | None = Field(default=None, max_length=500)
    parent_id: UUID | None = Field(default=_UNSET)  # type: ignore[assignment]
    sort_order: float | None = None
    icon: str | None = Field(default=_UNSET, max_length=100)  # type: ignore[assignment]
    cover_url: str | None = Field(default=_UNSET, max_length=2000)  # type: ignore[assignment]

    @field_validator("sort_order")
    @classmethod
    def sort_order_finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("sort_order must be a finite number")
        return v

    @field_validator("cover_url")
    @classmethod
    def cover_url_safe_scheme(cls, v: str | None) -> str | None:
        if v is None or v is _UNSET:
            return v
        if not v.startswith("https://"):
            raise ValueError("cover_url must use https:// scheme")
        return v

    @field_validator("title")
    @classmethod
    def normalize_title(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = " ".join(v.split())
        if not v:
            raise ValueError("Title must not be empty")
        return v


class PublishRequest(BaseModel):
    workspace_id: UUID


# ------------------------------------------------------------------
# Response schemas
# ------------------------------------------------------------------

class PageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_id: UUID | None
    title: str
    type: PageType
    icon: str | None
    cover_url: str | None
    is_private: bool
    workspace_id: UUID | None
    sort_order: float
    google_drive_id: str
    created_at: datetime
    updated_at: datetime


class PageTreeNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_id: UUID | None
    title: str
    type: PageType
    icon: str | None
    is_private: bool
    workspace_id: UUID | None
    sort_order: float
    google_drive_id: str
    children: list[PageTreeNode] = []
